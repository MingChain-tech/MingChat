/** Runtime API stub — P2P tools are exported directly from api.js */
let _runtime = null;
export function setP2PRuntime(runtime) { _runtime = runtime; }
export function getP2PRuntime() { return _runtime; }
