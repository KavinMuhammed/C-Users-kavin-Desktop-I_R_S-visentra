const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const root = path.join(__dirname,'../../apps/web');
async function harness(){
  const field = await import(pathToFileURL(path.join(root,'src/modules/nexus_field.mjs')).href);
  const source = fs.readFileSync(path.join(root,'vendor/three/three.module.min.js'),'utf8');
  return {...field,THREE:await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))};
}
test('particle layout is bounded, deterministic and contains finite spatial coordinates',async()=>{
  const {fieldLayout}=await harness();
  for(const n of [-10,0,1100,2800,99999,Infinity,NaN]){
    const a=fieldLayout(n),b=fieldLayout(n);
    assert.ok(a.count>=0&&a.count<=3200);
    assert.deepEqual(a.position,b.position);
    assert.equal(a.position.length,a.count*3);
    assert.ok([...a.position,...a.destination].every(v=>Number.isFinite(v)&&Math.abs(v)<=24));
  }
});
test('reduced motion resolves immediately; pause freezes buffers and shader time',async()=>{
  const {THREE,createNexusField}=await harness();
  const staticField=createNexusField(THREE,{reduced:true,entrance:true});
  staticField.update(100,true);
  assert.equal(staticField.points.material.uniforms.time.value,0);
  assert.equal(staticField.points.material.uniforms.resolve.value,1);
  const field=createNexusField(THREE,{entrance:true});
  const buffer=field.points.geometry.attributes.position.array;
  field.update(.1,true);const time=field.points.material.uniforms.time.value;
  field.update(100,false);
  assert.equal(field.points.material.uniforms.time.value,time);
  assert.equal(field.points.geometry.attributes.position.array,buffer);
  for(let i=0;i<100;i++)field.update(.1,true);
  assert.equal(field.points.material.uniforms.resolve.value,1);
  assert.equal(field.points.material.depthWrite,false);
  let released=0;
  field.points.geometry.addEventListener('dispose',()=>released++);
  field.points.material.addEventListener('dispose',()=>released++);
  field.points.geometry.dispose();field.points.material.dispose();
  assert.equal(released,2);
});
