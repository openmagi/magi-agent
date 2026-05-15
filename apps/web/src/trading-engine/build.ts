import { build } from 'esbuild'
import { cpSync, mkdirSync } from 'fs'

const SKILL_OUT = '../lib/templates/skills/trading/engine'

async function main(): Promise<void> {
  mkdirSync(SKILL_OUT, { recursive: true })

  await build({
    entryPoints: ['src/index.ts'],
    bundle: true,
    platform: 'node',
    target: 'node22',
    format: 'esm',
    outfile: `${SKILL_OUT}/index.mjs`,
    external: ['ws'],
    sourcemap: true,
    minify: false,
  })

  cpSync('package.runtime.json', `${SKILL_OUT}/package.json`)
  console.log('Build complete → ' + SKILL_OUT)
}

main()
