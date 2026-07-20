import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync("frontend/index.html", "utf8");
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];

scripts.forEach((match, index) => {
  new vm.Script(match[1], { filename: `inline-${index}.js` });
});

console.log(`JavaScript syntax OK: ${scripts.length} scripts`);
