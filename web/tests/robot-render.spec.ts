import { expect, test } from '@playwright/test';
import { container, readyPage } from './model-stack.helpers';

/**
 * The robot is drawn as its catalogue asset, found through 1.8's own chain.
 *
 * The robot's entity says `content_refs = ["catalog:dcef7c31-…"]`. Until the
 * 1.8 migration that resolved through the demo's 1.7 catalogue query; now it
 * is an inventory read by id (`POST /v1/content/query`), then the item's
 * content manifest from the resolver (`GET /v1/content/resolve?uri=`), whose
 * `assets[0]` is the glTF. This is the migration's acceptance for the
 * catalogue leg, kept as a test: the TurtleBot draws, and it got its asset by
 * that route rather than any other.
 *
 * Skips unless the bridge stack is up. Either robot -- the ROS 2 tier or the
 * bridge's kinematic one -- carries the same reference.
 */

const ROBOT = 'ent:robot:tb3';
const CONTENT_ID = 'dcef7c31-1d5f-54aa-9344-899912f9d34a';

test('the TurtleBot draws from its content manifest', async ({ page }) => {
  test.skip(container() === null, 'bridge stack not running');
  test.setTimeout(150_000);

  const resolved: string[] = [];
  page.on('requestfinished', async (req) => {
    const url = new URL(req.url());
    if (url.pathname.endsWith('/v1/content/resolve')
        && (url.searchParams.get('uri') || '').endsWith(`/content/${CONTENT_ID}`)
        && (await req.response())?.status() === 200) {
      resolved.push(url.searchParams.get('uri')!);
    }
  });

  await readyPage(page);
  await page.waitForFunction((id) => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id) === id);
  }, ROBOT, { timeout: 60_000 });

  const uri = await page.waitForFunction((id) => {
    const v = (window as any).__viewer;
    // The model rides a sibling entity, `<id>-model`, beside the label.
    const e = v.entities.values.find((x: any) => String(x.id) === `${id}-model`);
    const u = e?.model?.uri?.getValue?.(v.clock.currentTime);
    return u ? String(u) : null;
  }, ROBOT, { timeout: 30_000 }).then((h) => h.jsonValue());

  expect(uri, 'the robot is drawn as a model, not a pin').toMatch(/models\/tb3\.glb$/);
  expect(resolved, 'its manifest came from the resolver').toContain(
    `spatialdds://catalog.example.com/demo/content/${CONTENT_ID}`);
});
