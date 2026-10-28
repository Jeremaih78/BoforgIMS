const test = require('node:test');
const assert = require('node:assert/strict');
const {createPreviewLoader, selectionState, safePath} = require('../../static/tasker/js/workbench.js');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
function fixture() {
  const pending = [], rendered = [], cleared = [], failed = [];
  const loader = createPreviewLoader({load: url => { const request = deferred(); pending.push({url, ...request}); return request.promise; }, render: data => rendered.push(data), clear: state => cleared.push(state), fail: () => failed.push(true)});
  return {loader, pending, rendered, cleared, failed};
}
test('a late preview cannot overwrite a more recently selected task', async () => {
  const f = fixture(), first = f.loader.open('/a/'), second = f.loader.open('/b/');
  f.pending[1].resolve({status:200,data:{title:'B'}}); await second;
  f.pending[0].resolve({status:200,data:{title:'A'}}); await first;
  assert.deepEqual(f.rendered, [{title:'B'}]);
  assert.deepEqual(f.cleared, [true,true]);
});
test('revoked preview access clears old content and displays no response data', async () => {
  const f = fixture(), request = f.loader.open('/task/');
  f.pending[0].resolve({status:403,data:{title:'Private'}}); await request;
  assert.deepEqual(f.rendered, []); assert.equal(f.failed.length, 1); assert.deepEqual(f.cleared, [true,false]);
});
test('closing a drawer invalidates in-flight work', async () => {
  const f = fixture(), request = f.loader.open('/task/'); f.loader.close();
  f.pending[0].resolve({status:200,data:{title:'Closed'}}); await request;
  assert.deepEqual(f.rendered, []); assert.equal(f.failed.length, 0);
});
test('an obsolete network error cannot erase a newer successful preview', async () => {
  const f = fixture(), first = f.loader.open('/a/'), second = f.loader.open('/b/');
  f.pending[1].resolve({status:200,data:{title:'B'}}); await second;
  f.pending[0].reject(new Error('Offline')); await first;
  assert.deepEqual(f.rendered, [{title:'B'}]); assert.deepEqual(f.failed, []);
});
test('malformed and redirected login responses leave a recoverable error', async () => {
  for (const response of [{status:200,data:{}},{status:302,data:null}]) {
    const f = fixture(), request = f.loader.open('/task/'); f.pending[0].resolve(response); await request;
    assert.equal(f.failed.length,1); assert.equal(f.rendered.length,0);
  }
});
test('selection counts only enabled controls and reports mixed selection', () => {
  assert.deepEqual(selectionState([{checked:true},{checked:false},{checked:true,disabled:true}]), {selected:1,all:false,mixed:true});
  assert.deepEqual(selectionState([{checked:true}]), {selected:1,all:true,mixed:false});
  assert.deepEqual(selectionState([]), {selected:0,all:false,mixed:false});
});
test('preview links allow only local absolute paths', () => {
  assert.equal(safePath('/tasks/example/?a=1'),true);
  for (const value of ['//evil.test','https://evil.test','javascript:alert(1)','/\\evil.test','/\n/evil','/\t/evil','/ /evil',null]) assert.equal(safePath(value),false);
});
