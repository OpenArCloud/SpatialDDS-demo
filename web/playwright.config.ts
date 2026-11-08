import { defineConfig } from '@playwright/test';

/** The specs that drive or read the one running venue; see `projects`. */
const STACK = /(model-stack|robot-goto|robot-captures|robot-render|nav-refusal|claims-stack|claims-overlay-stack)\.spec\.ts/;

export default defineConfig({
  testDir: './tests',
  // Always rebuild; see tests/global-setup.ts for why this is not left to
  // the webServer command below.
  globalSetup: './tests/global-setup.ts',
  timeout: 90_000,
  expect: {
    timeout: 15_000
  },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: true
  },
  // Two projects, because two kinds of test live here.
  //
  // Most specs are self-contained: mock endpoints, parsers, a page and its
  // own fixtures. They can run as wide as the machine allows.
  //
  // Three of them drive the *one* running venue -- they move ducks, reshape
  // the pond and send the robot across the plaza -- and Playwright runs
  // files in parallel by default. `model-stack.spec.ts` already collected
  // its own tests into one serial file for this reason; what that could not
  // do is keep a second *file* out of the world at the same time. It fired
  // the first time two files drove the robot at once: a capture asked for a
  // journey to the far side of the basin while the tap-to-send spec sent the
  // same robot somewhere else, and the journey reported 442 samples that
  // never arrived anywhere. One robot, two writers, and a test failure that
  // pointed at neither.
  //
  // Per-project `workers: 1` is Playwright's own answer for a shared
  // resource, and this stack is exactly that -- one bus, one model service,
  // one robot.
  //
  // `robot-render` and `nav-refusal` joined it on 2026-11-08. Both read the
  // running venue (the first localizes against it, the second borrows its
  // live robot record) and were in `isolated`, so they ran beside the stack
  // specs. In the claims batch, against a private stack, both timed out
  // waiting for the page (no localization in 40 s, no `model:loaded` in
  // 90 s) when run beside robot-goto, and both passed run serially. Which
  // shared thing they contended for was not isolated; that they share the
  // venue is enough to put them here.
  projects: [
    {
      name: 'stack',
      testMatch: STACK,
      workers: 1
    },
    {
      name: 'isolated',
      testIgnore: STACK
    }
  ],
  webServer: {
    command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173',
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000
    // No `env` here on purpose. This command does not run when a preview
    // server is already listening, so anything the bundle needs at build
    // time belongs in tests/global-setup.ts, which always runs --- the
    // command lane's token included.
  }
});
