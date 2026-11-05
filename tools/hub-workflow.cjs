const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const python = path.join(root, '.venv-dev', 'Scripts', 'python.exe');
if (!fs.existsSync(python)) {
  process.stderr.write('缺少项目的 .venv-dev Python 环境，请先运行 tools/setup-development-environment.ps1。\n');
  process.exit(1);
}
const result = spawnSync(python, [path.join(__dirname, 'hub_workflow_report.py')], {
  cwd: root,
  env: process.env,
  stdio: 'inherit',
  windowsHide: true,
});
if (result.error) {
  process.stderr.write(`诊断启动失败：${result.error.message}\n`);
}
process.exit(result.status ?? 1);
