"""Writing engines. Both are the CLIs already signed in on this machine, so a run costs
subscription usage, not an API bill. Each run happens in an empty temp directory with no tools,
so the model can only write text: it cannot read files, browse, or run commands.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

SYSTEM = ("You are an expert CV writer working for one candidate. Follow the brief in the user message "
          "exactly. Reply with one JSON object only: no prose, no markdown fences.")
INSTRUCTION = "Follow the brief provided on stdin and reply with the JSON object only."


class EngineError(RuntimeError):
    pass


class Engine(Protocol):
    id: str
    label: str

    def available(self) -> bool: ...
    def run(self, brief: str, timeout: int = 420, tools: list[str] | None = None) -> str: ...


def _run(cmd: list[str], brief: str, timeout: int, env: dict | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="cvtailor-") as cwd:
        try:
            return subprocess.run(cmd, input=brief, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, cwd=cwd, env=env,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise EngineError(f"The model took longer than {timeout}s. Try again, or switch engine.") from e


class ClaudeCLI:
    def __init__(self, model: str):
        self.model = model
        self.id = f"claude:{model}"
        self.label = f"Claude {model.capitalize()}"

    def available(self) -> bool:
        return shutil.which("claude") is not None

    def run(self, brief: str, timeout: int = 420, tools: list[str] | None = None) -> str:
        """`tools` is only ever ["WebSearch"] (job discovery); writing runs get no tools at all."""
        exe = shutil.which("claude")
        if not exe:
            raise EngineError("The claude CLI is not on PATH.")
        allowed = ",".join(tools or [])
        cmd = [exe, "-p", INSTRUCTION, "--output-format", "json", "--model", self.model,
               "--tools", allowed, "--system-prompt", SYSTEM, "--no-session-persistence",
               "--safe-mode", "--permission-prompts", "none", "--effort", "medium" if tools else "high"]
        if tools:
            cmd += ["--allowedTools", allowed]
        proc = _run(cmd, brief, timeout)
        try:
            env = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise EngineError(f"Claude CLI returned no JSON envelope. stderr: {proc.stderr[-800:]}"
                              f" stdout: {proc.stdout[-800:]}")
        if env.get("is_error") or proc.returncode != 0:
            raise EngineError(f"Claude CLI error: {str(env.get('result') or proc.stderr)[-800:]}")
        return env.get("result", "")


def _gemini_entry() -> Path | None:
    roots = []
    npm = shutil.which("npm")
    if npm:
        try:
            out = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=20, shell=npm.lower().endswith((".cmd", ".bat")))
            roots.append(Path(out.stdout.strip()))
        except (OSError, subprocess.TimeoutExpired):
            pass
    if os.environ.get("APPDATA"):
        roots.append(Path(os.environ["APPDATA"]) / "npm" / "node_modules")
    for r in roots:
        p = r / "@google" / "gemini-cli" / "bundle" / "gemini.js"
        if p.exists():
            return p
    return None


class GeminiCLI:
    """Gemini CLI, signed in with the Google account (AI Pro/Ultra plans raise its limits).
    Called through node directly: the npm .cmd/.ps1 shims mangle long arguments on Windows."""

    def __init__(self, model: str = ""):
        self.model = model
        self.id = f"gemini:{model or 'default'}"
        self.label = f"Gemini {model}" if model else "Gemini (CLI default model)"
        self._entry = None

    def available(self) -> bool:
        if self._entry is None:
            self._entry = _gemini_entry() or False
        return bool(self._entry) and shutil.which("node") is not None

    def run(self, brief: str, timeout: int = 420, tools: list[str] | None = None) -> str:
        if not self.available():
            raise EngineError("Gemini CLI is not installed (npm install -g @google/gemini-cli).")
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(SYSTEM)
            system_path = f.name
        try:
            env = os.environ | {"GEMINI_SYSTEM_MD": system_path}
            cmd = [shutil.which("node"), str(self._entry), "-p", INSTRUCTION, "-o", "json",
                   "--approval-mode", "plan", "--skip-trust"]
            if self.model:
                cmd += ["-m", self.model]
            proc = _run(cmd, brief, timeout, env=env)
        finally:
            os.unlink(system_path)
        if "IneligibleTierError" in proc.stderr or "no longer supported for Gemini Code Assist" in proc.stderr:
            raise EngineError("Google no longer lets Gemini CLI run on a personal Google sign-in (it moved individuals "
                              "to Antigravity). Switch to Claude at the top, or add a Gemini API key from Google AI "
                              "Studio to use Gemini here.")
        try:
            payload = json.loads(proc.stdout[proc.stdout.find("{"):])
        except json.JSONDecodeError:
            hint = " Run `gemini` once in a terminal to sign in with Google." if "auth" in (proc.stderr + proc.stdout).lower() else ""
            raise EngineError(f"Gemini CLI returned no JSON.{hint} stderr: {proc.stderr[-800:]}")
        if payload.get("error"):
            msg = json.dumps(payload["error"])
            if "auth method" in msg.lower():
                raise EngineError("Gemini CLI is not signed in. Open a terminal, run `gemini`, choose "
                                  "'Sign in with Google' with your subscription account, then try again.")
            raise EngineError(f"Gemini CLI error: {msg[:800]}")
        return payload.get("response", "")


ENGINES: dict[str, Engine] = {e.id: e for e in (ClaudeCLI("opus"), ClaudeCLI("sonnet"), GeminiCLI())}


def chosen_family() -> str:
    """The top bar's Claude/Gemini switch, as saved in the profile's preferences."""
    try:
        from .profile import load_profile
        eng = ((load_profile().get("preferences") or {}).get("defaults") or {}).get("engine", "claude:opus")
    except Exception:
        eng = "claude:opus"
    return "gemini" if eng.startswith("gemini") else "claude"


def light_engine() -> str:
    """For quick reading jobs (a job page, a list of postings): the fast model of the chosen family."""
    return "gemini:default" if chosen_family() == "gemini" else "claude:sonnet"


def get_engine(engine_id: str) -> Engine:
    if engine_id not in ENGINES:
        raise EngineError(f"Unknown engine {engine_id}")
    return ENGINES[engine_id]


def parse_json_reply(text: str) -> dict:
    """The reply should be bare JSON; tolerate fences or a stray sentence around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise EngineError("The model's reply contained no JSON object.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise EngineError(f"The model's JSON did not parse: {e}") from e
