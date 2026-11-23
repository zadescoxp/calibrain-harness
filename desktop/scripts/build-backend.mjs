// Freeze the Python backend (ctb.server) into a self-contained folder with PyInstaller:
//   desktop/backend-dist/ctb-backend/ctb-backend[.exe]
// Uses its own build venv (desktop/.build-venv) so the dev .venv is never touched.
// PYTHON=/path/to/python3.12 picks the interpreter (default: python3, or `py -3` on Windows).
import { execFileSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..");
const win = process.platform === "win32";
const venv = path.join(desktop, ".build-venv");
const vpy = win ? path.join(venv, "Scripts", "python.exe") : path.join(venv, "bin", "python");

function run(cmd, args, opts = {}) {
  console.log(`$ ${cmd} ${args.join(" ")}`);
  execFileSync(cmd, args, { stdio: "inherit", cwd: desktop, ...opts });
}

if (!existsSync(vpy)) {
  const [py, ...pyArgs] = process.env.PYTHON ? [process.env.PYTHON] : win ? ["py", "-3"] : ["python3"];
  run(py, [...pyArgs, "-m", "venv", venv]);
}
run(vpy, ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]);
run(vpy, ["-m", "pip", "install", "--quiet", "aiohttp>=3.10", "pyinstaller>=6.10"]);

const out = path.join(desktop, "backend-dist");
rmSync(path.join(out, "ctb-backend"), { recursive: true, force: true });
run(vpy, [
  "-m", "PyInstaller",
  "--noconfirm", "--clean", "--onedir", "--console",
  "--name", "ctb-backend",
  "--distpath", out,
  "--workpath", path.join(desktop, ".pyi-build"),
  "--specpath", path.join(desktop, ".pyi-build"),
  "--paths", repo,
  "--collect-submodules", "ctb",
  "--add-data", `${path.join(repo, "ctb", "kairos", "assets")}:ctb/kairos/assets`,
  "--exclude-module", "pytest",
  "--exclude-module", "tkinter",
  path.join(desktop, "backend", "entry.py"),
]);
console.log(`backend frozen into ${path.join(out, "ctb-backend")}`);
