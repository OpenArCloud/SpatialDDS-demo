import { defineConfig } from '@playwright/test';

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
  projects: [
    {
      name: 'stack',
      testMatch: /(model-stack|robot-goto|robot-captures)\.spec\.ts/,
      workers: 1
    },
    {
      name: 'isolated',
      testIgnore: /(model-stack|robot-goto|robot-captures)\.spec\.ts/
    }
  ],
  webServer: {
    command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173',
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000
  }
});
