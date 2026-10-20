// Self-check: node web/test.mjs
import assert from "node:assert/strict";
import { parseRocking, parseBattery, buildRockingCmd, buildDriveCmd, hex } from "./priam_ble.js";

// Real frame from the TUI log: MED, 500 min, continue-on-disconnect.
const cmd = buildRockingCmd(2, 500, true);
assert.equal(hex(cmd), "1230753075");
const st = parseRocking(cmd);
assert.deepEqual([st.intensityName, st.setTimeMin, st.timeLeftMin, st.disc, st.error], ["MED", 500, 500, true, "none"]);
assert.equal(hex(buildRockingCmd(0, 0, false)), "0000000000");
assert.equal(parseRocking(new Uint8Array([0x94, 0, 0, 0, 0])).error, "brake not engaged");
assert.equal(parseRocking(new Uint8Array(3)), null);
assert.throws(() => buildRockingCmd(1, 501, false));
assert.equal(parseBattery(new Uint8Array([0, 0, 0, 193])).percent, 100); // 3860 mV
assert.equal(parseBattery(new Uint8Array([0, 0, 0, 177])).percent, 60);  // 3540 mV
assert.equal(hex(buildDriveCmd(3)), "03");
console.log("ok");
