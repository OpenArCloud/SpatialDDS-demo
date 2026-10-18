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
/**
 * The command lane's credential, baked in here because this is where the
 * build is.
 *
 * Vite inlines `import.meta.env.VITE_*` at build time, so a token supplied to
 * the *preview* server arrives far too late: the bundle is already compiled
 * with `||""` in its place, the page's Send Robot button is correctly inert,
 * and tap-to-send fails on a missing credential that looks for all the world
 * like a bridge problem. Reading the venue over N.3/N.5 needs nothing --- the
 * bridge classifies those lanes `public-cacheable` --- and only commanding is
 * `operator-scoped`. Matches SPATIALDDS_BINDING_TOKEN's default in
 * run_bridge_server_docker.sh.
 *
 * Noted because it is this file's own lesson a third time: the first fix for
 * it went into `webServer.command`, which the docstring above says outright
 * does not always run. The decision has to be removed, not remembered.
 */
const BINDING_TOKEN = process.env.SPATIALDDS_BINDING_TOKEN || 'bridge-session';

export default function build() {
  execSync('npm run build', { cwd: new URL('..', import.meta.url).pathname,
                              stdio: 'inherit',
                              env: { ...process.env,
                                     VITE_SPATIALDDS_BINDING_TOKEN: BINDING_TOKEN } });
}
