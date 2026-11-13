import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';
import vm from 'node:vm';

const root = dirname(fileURLToPath(import.meta.url));

function loadTypeScriptModule(filename, { dependencies = {}, self } = {}) {
  const path = resolve(root, '..', 'src', filename);
  const source = readFileSync(path, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText;
  const loaded = { exports: {} };
  vm.runInNewContext(output, {
    module: loaded,
    exports: loaded.exports,
    require: name => {
      if (Object.hasOwn(dependencies, name)) return dependencies[name];
      throw new Error(`Unexpected dependency: ${name}`);
    },
    self,
  }, { filename: path });
  return loaded.exports;
}

const coordinates = loadTypeScriptModule('coordinateContract.ts');

test('WGS84 coordinates accept inclusive edges and reject invalid values', () => {
  const { isValidWgs84Coordinate } = coordinates;
  assert.equal(isValidWgs84Coordinate(-180, -90), true);
  assert.equal(isValidWgs84Coordinate(180, 90), true);
  assert.equal(isValidWgs84Coordinate(-180.0001, 0), false);
  assert.equal(isValidWgs84Coordinate(180.0001, 0), false);
  assert.equal(isValidWgs84Coordinate(0, -90.0001), false);
  assert.equal(isValidWgs84Coordinate(0, 90.0001), false);
  assert.equal(isValidWgs84Coordinate(Number.NaN, 0), false);
  assert.equal(isValidWgs84Coordinate(0, Number.POSITIVE_INFINITY), false);
  assert.equal(isValidWgs84Coordinate('1', 0), false);
});

test('bin worker excludes invalid coordinates and preserves valid edge longitudes', () => {
  const responses = [];
  const self = { postMessage: response => responses.push(response) };
  const worker = loadTypeScriptModule('binWorker.ts', { dependencies: {
    './coordinateContract': coordinates,
  }, self });
  self.onmessage({ data: {
    type: 'init',
    lon: [-180, 180, 180.0001, Number.NaN, Number.POSITIVE_INFINITY, 0, 0, 0, 0],
    lat: [0, 0, 0, 0, 0, 90.1, Number.NaN, 85, -85],
  } });
  self.onmessage({ data: {
    type: 'bin',
    reqId: 7,
    zoom: 0,
    dpr: 1,
    still: [0, 1, 2, 3, 4, 5, 6, 7, 8],
    live: [],
  } });

  assert.equal(responses.length, 1);
  assert.equal(responses[0].reqId, 7);
  assert.deepEqual(Array.from(responses[0].still.idx), [0, 1, 7, 8]);
  assert.deepEqual(Array.from(responses[0].still.count), [1, 1, 1, 1]);
  assert.deepEqual(Array.from(responses[0].live.idx), []);
});
