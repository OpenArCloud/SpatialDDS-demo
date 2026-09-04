import { execSync } from 'node:child_process';

/**
 * Build before every run, whether or not the preview server is reused.
 *
 * Playwright serves the *built* bundle on :4173 and reuses a server that is
 * already listening. That is fast and correct for the server, and wrong for
 * the bundle: with a preview server left over from an earlier run, the
 * webServer command never fires, nothing rebuilds, and the specs quietly
 * exercise whatever dist happened to contain.
 *
 * This cost two rounds of "my change had no effect" in P2.3 and two more in
 * P3.4 -- the second time with the trap already written up in CONTRIBUTING,
 * by the person who wrote it up. Documentation is a warning sign, not a
 * guardrail. The fix is to remove the decision rather than to remember
 * harder.
 */
export default function build() {
  execSync('npm run build', { cwd: new URL('..', import.meta.url).pathname,
                              stdio: 'inherit' });
}
