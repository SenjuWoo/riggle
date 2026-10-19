// The 3D stage: model, camera, skeleton handles, placement picking and the
// spring preview that makes jiggle bones feel alive before export.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export class Viewport {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0c0f13);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.01, 500);
    this.camera.position.set(0, 1.4, 3.6);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.target.set(0, 0.9, 0);
    this.controls.maxPolarAngle = Math.PI * 0.92;

    const hemi = new THREE.HemisphereLight(0xcfe0ff, 0x241b16, 1.15);
    this.scene.add(hemi);

    const key = new THREE.DirectionalLight(0xfff6ec, 2.4);
    key.position.set(3.5, 6, 4.5);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.near = 0.5;
    key.shadow.camera.far = 30;
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.02;
    this.key = key;
    this.scene.add(key);

    const rim = new THREE.DirectionalLight(0x6ee7ff, 1.3);
    rim.position.set(-4, 2.4, -4.5);
    this.scene.add(rim);

    const fill = new THREE.DirectionalLight(0xff9ecb, 0.55);
    fill.position.set(-3, 1.4, 4);
    this.scene.add(fill);

    this.grid = new THREE.GridHelper(20, 40, 0x39424f, 0x1b2129);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.55;
    this.scene.add(this.grid);

    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(30, 64).rotateX(-Math.PI / 2),
      new THREE.ShadowMaterial({ opacity: 0.38 }),
    );
    ground.receiveShadow = true;
    this.ground = ground;
    this.scene.add(ground);

    this.loader = new GLTFLoader();
    this.model = null;
    this.modelBaseRotY = 0;
    this.joints = [];
    this.jointSpheres = [];
    this.byName = new Map();
    this.selectedJoint = null;
    this.jointEditable = false;
    this.mirror = true;
    this.placement = false;

    this.rigGroup = new THREE.Group();
    this.scene.add(this.rigGroup);
    this.lineGeo = new THREE.BufferGeometry();
    this.lineSegments = new THREE.LineSegments(
      this.lineGeo,
      new THREE.LineBasicMaterial({ color: 0x9fb0c3, transparent: true, opacity: 0.85, depthTest: false }),
    );
    this.lineSegments.renderOrder = 998;
    this.rigGroup.add(this.lineSegments);

    this.transform = new TransformControls(this.camera, canvas);
    this.transform.setSize(0.72);
    this.transform.addEventListener('dragging-changed', (e) => {
      this.controls.enabled = !e.value;
    });
    this.transform.addEventListener('objectChange', () => this.dragSelected());
    this.scene.add(this.transform.getHelper());

    this.marker = new THREE.Mesh(
      new THREE.SphereGeometry(1, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xff6ec7, transparent: true, opacity: 0.9, depthTest: false }),
    );
    this.marker.renderOrder = 1000;
    this.marker.visible = false;
    this.scene.add(this.marker);
    this.radiusRing = new THREE.Mesh(
      new THREE.SphereGeometry(1, 20, 14),
      new THREE.MeshBasicMaterial({ color: 0xff6ec7, wireframe: true, transparent: true, opacity: 0.28, depthTest: false }),
    );
    this.radiusRing.visible = false;
    this.radiusRing.renderOrder = 1000;
    this.scene.add(this.radiusRing);

    this.onJointChange = () => {};
    this.onSelect = () => {};
    this.onSurface = () => {};

    this._paintTexture = null;
    this.jiggle = [];
    this.jiggleOn = false;
    this.jiggleTime = 0;

    this._bindPointer();
    this._resize();
    new ResizeObserver(() => this._resize()).observe(canvas.parentElement);
    this.clock = new THREE.Clock();
    this._tick();
  }

  // -- model -------------------------------------------------------------
  async load(url) {
    const gltf = await this.loader.loadAsync(url);
    if (this.model) {
      this.scene.remove(this.model);
      this._dispose(this.model);
    }
    this.model = gltf.scene;
    this.model.traverse((o) => {
      if (o.isMesh) {
        o.castShadow = true;
        o.receiveShadow = true;
        if (o.material) o.material.side = THREE.FrontSide;
      }
    });
    this.modelBaseRotY = this.model.rotation.y;
    this.scene.add(this.model);
    this.frame();
    if (this._paintTexture) this.applyPaintTexture(this._paintTexture);
    return gltf;
  }

  _dispose(root) {
    root.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
      // Textures are shared with the paint board and the next load; keep them.
      for (const m of mats) m.dispose();
    });
  }

  frame() {
    if (!this.model) return;
    const box = new THREE.Box3().setFromObject(this.model);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z, 0.1);
    const fov = (this.camera.fov * Math.PI) / 180;
    const dist = (maxDim / (2 * Math.tan(fov / 2))) * 1.35;
    this.controls.target.copy(center);
    this.camera.position.set(center.x + maxDim * 0.22, center.y + maxDim * 0.08, center.z + dist);
    this.camera.near = Math.max(0.001, maxDim / 2000);
    this.camera.far = maxDim * 120;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.key.shadow.camera.left = -maxDim;
    this.key.shadow.camera.right = maxDim;
    this.key.shadow.camera.top = maxDim;
    this.key.shadow.camera.bottom = -maxDim;
    this.key.shadow.camera.updateProjectionMatrix();
  }

  modelHeight() {
    if (!this.model) return 1;
    const box = new THREE.Box3().setFromObject(this.model);
    return Math.max(box.getSize(new THREE.Vector3()).y, 0.01);
  }

  setWireframe(on) {
    if (!this.model) return;
    this.model.traverse((o) => {
      if (!o.isMesh) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) if (m && 'wireframe' in m) m.wireframe = on;
    });
  }

  setGrid(on) {
    this.grid.visible = on;
    this.ground.visible = on;
  }

  // -- skeleton ----------------------------------------------------------
  setJoints(joints, editable) {
    this.clearRig();
    this.joints = joints || [];
    this.jointEditable = !!editable;
    this.byName = new Map(this.joints.map((j) => [j.name, j]));
    const r = this.modelHeight() * 0.012;
    for (const joint of this.joints) {
      const mat = new THREE.MeshBasicMaterial({
        color: joint.jiggle ? 0xff6ec7 : 0xc8ff4d,
        depthTest: false,
        transparent: true,
        opacity: 0.95,
      });
      const sphere = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 10), mat);
      sphere.renderOrder = 999;
      sphere.position.set(joint.head[0], joint.head[1], joint.head[2]);
      sphere.userData.joint = joint;
      this.rigGroup.add(sphere);
      this.jointSpheres.push(sphere);
    }
    this.rebuildLines();
    if (this.selectedJoint) this.selectJoint(this.selectedJoint.name);
  }

  clearRig() {
    this.transform.detach();
    for (const s of this.jointSpheres) {
      this.rigGroup.remove(s);
      s.geometry.dispose();
      s.material.dispose();
    }
    this.jointSpheres = [];
    this.selectedJoint = null;
    this.lineGeo.setAttribute('position', new THREE.Float32BufferAttribute([], 3));
  }

  rebuildLines() {
    const pts = [];
    const hasChild = new Set(this.joints.filter((j) => j.parent).map((j) => j.parent));
    for (const j of this.joints) {
      if (j.parent && this.byName.has(j.parent)) {
        const p = this.byName.get(j.parent);
        pts.push(...p.head, ...j.head);
      }
      if (!hasChild.has(j.name) && j.tail) pts.push(...j.head, ...j.tail);
    }
    this.lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    this.lineGeo.computeBoundingSphere();
  }

  syncSphere(joint) {
    const sphere = this.jointSpheres.find((s) => s.userData.joint === joint);
    if (sphere) sphere.position.set(joint.head[0], joint.head[1], joint.head[2]);
  }

  selectJoint(name) {
    const joint = this.byName.get(name);
    if (!joint) return;
    this.selectedJoint = joint;
    const sphere = this.jointSpheres.find((s) => s.userData.joint === joint);
    for (const s of this.jointSpheres) {
      s.material.color.set(s.userData.joint.jiggle ? 0xff6ec7 : 0xc8ff4d);
      s.scale.setScalar(1);
    }
    if (sphere) {
      sphere.material.color.set(0xffffff);
      sphere.scale.setScalar(1.35);
      if (this.jointEditable) this.transform.attach(sphere);
    }
    this.onSelect(joint);
  }

  dragSelected() {
    if (!this.jointEditable || !this.selectedJoint) return;
    const sphere = this.transform.object;
    if (!sphere) return;
    const joint = this.selectedJoint;
    const dx = sphere.position.x - joint.head[0];
    const dy = sphere.position.y - joint.head[1];
    const dz = sphere.position.z - joint.head[2];
    if (Math.abs(dx) + Math.abs(dy) + Math.abs(dz) < 1e-7) return;
    this._moveSubtree(joint, dx, dy, dz);
    if (this.mirror) this._mirror(joint.name.endsWith('.R') ? 'R' : 'L');
    for (const j of this.joints) this.syncSphere(j);
    this.rebuildLines();
    this.onJointChange(this.joints);
  }

  _moveSubtree(joint, dx, dy, dz) {
    const stack = [joint];
    while (stack.length) {
      const j = stack.pop();
      j.head = [j.head[0] + dx, j.head[1] + dy, j.head[2] + dz];
      j.tail = [j.tail[0] + dx, j.tail[1] + dy, j.tail[2] + dz];
      for (const child of this.joints) if (child.parent === j.name) stack.push(child);
    }
  }

  _mirror(source = 'L') {
    const suffix = source === 'R' ? '.R' : '.L';
    const other = source === 'R' ? '.L' : '.R';
    for (const j of this.joints) {
      if (!j.name.endsWith(suffix)) continue;
      const twin = this.byName.get(j.name.slice(0, -2) + other);
      if (!twin) continue;
      twin.head = [-j.head[0], j.head[1], j.head[2]];
      twin.tail = [-j.tail[0], j.tail[1], j.tail[2]];
    }
  }

  // -- picking -----------------------------------------------------------
  setPlacement(on) {
    this.placement = !!on;
    this.canvas.style.cursor = on ? 'crosshair' : 'default';
    if (!on) this.hideMarker();
  }

  showMarker(point, radius) {
    this.marker.position.set(point[0], point[1], point[2]);
    this.marker.scale.setScalar(Math.max(this.modelHeight() * 0.012, 0.005));
    this.marker.visible = true;
    if (radius) {
      this.radiusRing.position.copy(this.marker.position);
      this.radiusRing.scale.setScalar(radius);
      this.radiusRing.visible = true;
    }
  }

  hideMarker() {
    this.marker.visible = false;
    this.radiusRing.visible = false;
  }

  _bindPointer() {
    this._down = null;
    this.canvas.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      this._down = { x: e.clientX, y: e.clientY };
    });
    this.canvas.addEventListener('pointerup', (e) => {
      if (!this._down || e.button !== 0) return;
      const moved = Math.hypot(e.clientX - this._down.x, e.clientY - this._down.y);
      this._down = null;
      if (moved > 5) return;
      const rect = this.canvas.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      );
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, this.camera);
      if (this.placement) {
        const hits = ray.intersectObject(this.model, true).filter((h) => h.object.isMesh);
        if (hits.length) {
          const hit = hits[0];
          const normal = hit.face
            ? hit.face.normal.clone().transformDirection(hit.object.matrixWorld)
            : new THREE.Vector3(0, 1, 0);
          this.onSurface({ point: hit.point.toArray(), normal: normal.toArray() });
        }
        return;
      }
      const jointHits = ray.intersectObjects(this.jointSpheres, false);
      if (jointHits.length) this.selectJoint(jointHits[0].object.userData.joint.name);
    });
  }

  // -- painting ----------------------------------------------------------
  applyPaintTexture(texture) {
    this._paintTexture = texture;
    if (!this.model) return;
    this.model.traverse((o) => {
      if (!o.isMesh) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) {
        if (!m) continue;
        m.map = texture;
        if (m.color) m.color.set(0xffffff);
        m.needsUpdate = true;
      }
    });
  }

  // -- jiggle ------------------------------------------------------------
  startJiggle(specs) {
    this.stopJiggle();
    this.jiggle = [];
    for (const spec of specs) {
      const bone = this.model ? this.model.getObjectByName(spec.name) : null;
      if (!bone || !bone.isBone) continue;
      this.jiggle.push({
        bone,
        rest: bone.quaternion.clone(),
        params: { stiffness: 0.55, damping: 0.35, gravity: 0.25, mass: 0.5, ...(spec.params || {}) },
        ang: [0, 0],
        vel: [0, 0],
        prev: null,
        prevVel: null,
      });
    }
    this.jiggleOn = this.jiggle.length > 0;
    this.jiggleTime = 0;
    return this.jiggle.length;
  }

  stopJiggle() {
    for (const j of this.jiggle) j.bone.quaternion.copy(j.rest);
    this.jiggle = [];
    this.jiggleOn = false;
    if (this.model) this.model.rotation.y = this.modelBaseRotY;
  }

  updateJiggleParams(paramsByName) {
    for (const j of this.jiggle) {
      if (paramsByName[j.bone.name]) j.params = { ...j.params, ...paramsByName[j.bone.name] };
    }
  }

  shake() {
    for (const j of this.jiggle) {
      j.vel[0] += (Math.random() - 0.5) * 14;
      j.vel[1] += (Math.random() - 0.5) * 14;
    }
  }

  _simulate(dt) {
    if (!this.jiggleOn) return;
    this.jiggleTime += dt;
    if (this.model) this.model.rotation.y = this.modelBaseRotY + Math.sin(this.jiggleTime * 0.9) * 0.05;
    const p = new THREE.Vector3();
    const q = new THREE.Quaternion();
    const e = new THREE.Euler();
    for (const j of this.jiggle) {
      const parent = j.bone.parent;
      if (!parent) continue;
      parent.getWorldPosition(p);
      if (!j.prev) {
        j.prev = p.clone();
        j.prevVel = new THREE.Vector3();
      }
      const vel = p.clone().sub(j.prev).divideScalar(Math.max(dt, 1e-4));
      const acc = vel.clone().sub(j.prevVel).divideScalar(Math.max(dt, 1e-4));
      j.prev.copy(p);
      j.prevVel.copy(vel);
      parent.getWorldQuaternion(q).invert();
      const local = acc.clone().applyQuaternion(q);
      const down = new THREE.Vector3(0, -1, 0).applyQuaternion(q);
      const k = 20 + 170 * j.params.stiffness;
      const c = 1.5 + 15 * j.params.damping;
      const inertia = 1 / Math.max(0.2, j.params.mass);
      const fx = (local.z * 0.8 + down.z * j.params.gravity * 5) * inertia;
      const fz = (-local.x * 0.8 - down.x * j.params.gravity * 5) * inertia;
      j.vel[0] += (-k * j.ang[0] - c * j.vel[0] + fx) * dt;
      j.vel[1] += (-k * j.ang[1] - c * j.vel[1] + fz) * dt;
      j.ang[0] = clamp(j.ang[0] + j.vel[0] * dt, -0.8, 0.8);
      j.ang[1] = clamp(j.ang[1] + j.vel[1] * dt, -0.8, 0.8);
      e.set(j.ang[0], 0, j.ang[1], 'XYZ');
      j.bone.quaternion.copy(j.rest).multiply(new THREE.Quaternion().setFromEuler(e));
    }
  }

  // -- loop --------------------------------------------------------------
  _resize() {
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const w = parent.clientWidth || 1;
    const h = parent.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _tick() {
    requestAnimationFrame(() => this._tick());
    const dt = Math.min(this.clock.getDelta(), 1 / 30);
    this.controls.update();
    this._simulate(dt);
    this.renderer.render(this.scene, this.camera);
  }
}
