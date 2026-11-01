/* Decorative spatial field; never sensor data or inference progress.
   One draw call, immutable buffers, deterministic layout, no frame allocations. */
export function fieldLayout(requested = 2800) {
  const count = Math.min(3200, Math.max(0, Math.floor(Number(requested) || 0)));
  const position = new Float32Array(count * 3), destination = new Float32Array(count * 3);
  const kind = new Float32Array(count), phase = new Float32Array(count);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const u = (i + .5) / count, theta = i * goldenAngle, y = 1 - 2 * u;
    const radius = Math.sqrt(1 - y * y), violet = i % 4 === 0;
    position.set([19 * radius * Math.cos(theta), 13 * y + 3, 19 * radius * Math.sin(theta)], i * 3);
    const band = (i % 13) / 12, angle = theta + band * .2;
    destination.set(violet
      ? [20 * Math.cos(angle), 3 + 9 * Math.sin(angle * 2) * (.4 + band * .6), 13 * Math.sin(angle)]
      : [(16 + band * 7) * Math.cos(angle), -1.5 + band * 3, (12 + band * 7) * Math.sin(angle)], i * 3);
    kind[i] = violet ? 1 : 0; phase[i] = (i * .61803398875) % 1;
  }
  return { count, position, destination, kind, phase };
}

export function createNexusField(THREE, { count = 2800, reduced = false, entrance = false } = {}) {
  const data = fieldLayout(count), geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(data.position, 3));
  geometry.setAttribute('destination', new THREE.BufferAttribute(data.destination, 3));
  geometry.setAttribute('kind', new THREE.BufferAttribute(data.kind, 1));
  geometry.setAttribute('phase', new THREE.BufferAttribute(data.phase, 1));
  const material = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: true,
    uniforms: { time: { value: 0 }, resolve: { value: entrance && !reduced ? 0 : 1 }, strength: { value: .65 }, fade: { value: entrance && !reduced ? 0 : 1 } },
    vertexShader: `attribute vec3 destination; attribute float kind; attribute float phase;
      uniform float time; uniform float resolve; uniform float fade; varying float vKind; varying float vAlpha;
      void main(){
        vec3 p=mix(position,destination,smoothstep(0.0,1.0,resolve));
        float angle=time*.025; mat2 spin=mat2(cos(angle),-sin(angle),sin(angle),cos(angle));
        p.xz=spin*p.xz;
        vec4 mv=modelViewMatrix*vec4(p,1.0);
        gl_Position=projectionMatrix*mv;
        gl_PointSize=clamp((1.4+phase*1.8)*42.0/max(12.0,-mv.z),1.0,3.5);
        vKind=kind; vAlpha=(.28+phase*.6)*smoothstep(0.0,.25,resolve+.08)*fade;
      }`,
    fragmentShader: `uniform float strength; varying float vKind; varying float vAlpha;
      void main(){
        float r=length(gl_PointCoord-vec2(.5)); if(r>.5) discard;
        vec3 gold=vec3(.95,.79,.48), violet=vec3(.65,.45,1.0);
        gl_FragColor=vec4(mix(gold,violet,vKind),vAlpha*strength*(1.0-smoothstep(.1,.5,r)));
      }`
  });
  const points = new THREE.Points(geometry, material);
  points.name = 'decorative-nexus-field'; points.frustumCulled = false;
  let elapsed = 0;
  return {
    points, count: data.count,
    // intro acts: I void (0–0.4 s, the field fades in) · II signal (0.4–3 s, sphere resolves to orbital bands)
    update(delta, moving) {
      if (!reduced && moving) elapsed += Math.min(.1, Math.max(0, delta));
      material.uniforms.time.value = elapsed;
      material.uniforms.fade.value = entrance && !reduced ? Math.min(1, elapsed / 1.1) : 1;
      material.uniforms.resolve.value = entrance && !reduced ? Math.min(1, Math.max(0, (elapsed - .4) / 2.6)) : 1;
    }
  };
}

/* Distant stars for hero scenes (intro, product page): static, one draw call, deterministic, ~1 µs per frame. */
export function createStarfield(THREE, { count = 700 } = {}) {
  const n = Math.min(1200, Math.max(0, Math.floor(count))), position = new Float32Array(n * 3), tone = new Float32Array(n);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const u = (i + .5) / n, y = 1 - 2 * u, r = Math.sqrt(1 - y * y), th = i * goldenAngle, d = 95 + ((i * 37) % 60);
    position.set([d * r * Math.cos(th), d * y * .62 + 8, d * r * Math.sin(th)], i * 3);
    tone[i] = ((i * 7919) % 100) / 100;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(position, 3));
  geometry.setAttribute("tone", new THREE.BufferAttribute(tone, 1));
  const material = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: true,
    vertexShader: `attribute float tone; varying float vTone;
      void main(){ vec4 mv=modelViewMatrix*vec4(position,1.0); gl_Position=projectionMatrix*mv; gl_PointSize=1.0+tone*1.6; vTone=tone; }`,
    fragmentShader: `varying float vTone;
      void main(){ float r=length(gl_PointCoord-vec2(.5)); if(r>.5) discard;
        vec3 c=mix(vec3(.95,.93,.88),vec3(.95,.82,.55),step(.82,vTone)); gl_FragColor=vec4(c,(.18+vTone*.42)*(1.0-smoothstep(.15,.5,r))); }`,
  });
  const points = new THREE.Points(geometry, material);
  points.name = "decorative-starfield"; points.frustumCulled = false;
  return { points, count: n };
}
