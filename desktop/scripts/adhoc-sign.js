// electron-builder afterPack hook. With no Developer ID certificate (CSC_LINK / CSC_NAME unset) electron-builder
// skips signing and leaves Electron's original signature broken by our changes, which Apple Silicon reports as
// "app is damaged". Ad-hoc sign the bundle instead so it runs (users still clear Gatekeeper once, see README).
// With a real certificate this does nothing and electron-builder signs (and can notarize) as usual.
const { execFileSync } = require("node:child_process");
const path = require("node:path");

exports.default = async function afterPack(ctx) {
  if (ctx.electronPlatformName !== "darwin") return;
  if (process.env.CSC_LINK || process.env.CSC_NAME) return;
  const app = path.join(ctx.appOutDir, `${ctx.packager.appInfo.productFilename}.app`);
  const ent = path.join(__dirname, "..", "build", "entitlements.mac.plist");
  console.log(`  • ad-hoc signing ${app} (no Developer ID certificate configured)`);
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", "--entitlements", ent, app], { stdio: "inherit" });
};
