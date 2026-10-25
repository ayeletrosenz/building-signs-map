"use strict";

let photoBoxes = [], streetLabelData = [], streetLabelLayer, layoutFrame;
const intersects = (a, b, gap = 0) => a.left < b.right + gap && a.right + gap > b.left
  && a.top < b.bottom + gap && a.bottom + gap > b.top;
const boxAt = (point, width, height = width) => ({ left: point.x - width / 2, right: point.x + width / 2,
  top: point.y - height / 2, bottom: point.y + height / 2 });

function photoDisplaySize(photo, baseSize) {
  const width=photo.thumbnailWidth||photo.width||baseSize;
  const height=photo.thumbnailHeight||photo.height||baseSize;
  const area=photo.thumbnailVisibleArea>0?photo.thumbnailVisibleArea:width*height;
  // Match visible sign area, including irregular outlines and transparent holes.
  // Limit unusually thin future cutouts so they cannot span the whole neighborhood.
  const scale=Math.min(baseSize/Math.sqrt(area),baseSize*3/Math.max(width,height));
  return {width:width*scale,height:height*scale};
}

function scheduleMapLayout() {
  cancelAnimationFrame(layoutFrame);
  layoutFrame = requestAnimationFrame(() => {
    layoutPhotos(photos.filter(matches).filter(photo => validCoordinates(coordinates(photo))));
  });
}

function layoutPhotos(located) {
  photoLayer.clearLayers();
  photoBoxes = [];
  const viewport=map.getSize(), zoom=map.getZoom();
  const scale=typeof getThumbnailScale==="function"?getThumbnailScale():1;
  const size=(zoom>=19?54:zoom>=18?48:zoom>=17?40:32)*scale;
  const groups=new Map();
  for(const photo of [...located].sort((a,b)=>a.id.localeCompare(b.id))) {
    const key=coordinates(photo).join(",");
    if(!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(photo);
  }
  for(const group of groups.values()) group.forEach((photo,index)=>{
    const actual=L.latLng(coordinates(photo)), anchor=map.latLngToContainerPoint(actual);
    const angle=2*Math.PI*index/group.length;
    // Give pairs a small diagonal separation; keep larger groups close to their point.
    const offset=group.length===2
      ? L.point(index===0?-10:10,index===0?-10:10)
      : group.length>1?L.point(Math.round(9*Math.cos(angle)),Math.round(9*Math.sin(angle))):L.point(0,0);
    const dimensions=photoDisplaySize(photo,size);
    const point=anchor.add(offset), box=boxAt(point,dimensions.width,dimensions.height);
    if(box.right>=0&&box.bottom>=0&&box.left<=viewport.x&&box.top<=viewport.y) photoBoxes.push(box);
    const marker=L.marker(map.containerPointToLatLng(point),{icon:photoIcon(photo,size),title:"פתיחת תמונה",alt:"פתיחת תמונה",keyboard:true,riseOnHover:true}).addTo(photoLayer);
    marker.photo=photo;marker.actualCoordinates=actual;marker.photoBox=box;
    marker.on("click",()=>{
      if(document.body.classList.contains("locating")) previewEditorPoint(actual);
      else openPhoto(photo);
    });
  });
  layoutStreetLabels();
}

function layoutStreetLabels() {
  if (!streetLabelLayer) return;
  streetLabelLayer.clearLayers();
  const zoom = map.getZoom(), viewport = map.getSize(), mobile = viewport.x <= 600;
  const limit = zoom <= 16 ? (mobile ? 4 : 8) : zoom === 17 ? (mobile ? 8 : 16) : (mobile ? 12 : 24);
  const occupied = [...photoBoxes, { left: viewport.x - 120, top: 0, right: viewport.x, bottom: 80 }];
  const priority = { motorway: 0, trunk: 0, primary: 1, secondary: 2, tertiary: 3,
    residential: 4, living_street: 5, unclassified: 5, pedestrian: 6 };
  const candidates = [];
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = '500 13px "The Basics", Arial, sans-serif';
  for (const feature of streetLabelData) {
    const { highway, name } = feature.properties;
    const rank = priority[highway];
    if (rank === undefined || (zoom <= 16 && rank > 3)) continue;
    const width = measure.measureText(name).width + 18;
    // Combine near-collinear survey nodes so a readable label can span them.
    const points = L.LineUtil.simplify(feature.geometry.coordinates.map(([lng, lat]) => map.latLngToContainerPoint([lat, lng])), 2);
    const positions = [];
    for (let i = 1; i < points.length; i++) {
      // Clipping supplies usable candidates even when a street crosses the viewport edges.
      const segment = L.LineUtil.clipSegment(points[i - 1], points[i], L.bounds([25, 25], [viewport.x - 25, viewport.y - 35]));
      if (!segment) continue;
      const [a, b] = segment, length = a.distanceTo(b);
      if (length < width) continue;
      let angle = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
      if (angle > 90) angle -= 180;
      if (angle < -90) angle += 180;
      positions.push({ point: a.add(b).divideBy(2), angle, length });
    }
    for (const position of positions.sort((a,b) => b.length-a.length).slice(0, 8)) {
      candidates.push({ ...position, rank, width, name, id: feature.id });
    }
  }
  candidates.sort((a, b) => a.rank - b.rank || b.length - a.length || a.id - b.id);
  const names = new Set();
  for (const candidate of candidates) {
    if (names.size >= limit) break;
    if (names.has(candidate.name)) continue;
    const radians = candidate.angle * Math.PI / 180;
    const width = Math.abs(candidate.width * Math.cos(radians)) + Math.abs(20 * Math.sin(radians));
    const height = Math.abs(candidate.width * Math.sin(radians)) + Math.abs(20 * Math.cos(radians));
    const box = boxAt(candidate.point, width, height);
    if (occupied.some(other => intersects(box, other, 12))) continue;
    const label = document.createElement("span");
    label.className = "street-label-text";
    label.dir = "auto";
    label.textContent = candidate.name;
    label.style.transform = `translate(-50%, -50%) rotate(${candidate.angle}deg)`;
    L.marker(map.containerPointToLatLng(candidate.point), { pane: "streetLabels", keyboard: false,
      interactive: false, icon: L.divIcon({ html: label, className: "street-label", iconSize: [0, 0], iconAnchor: [0, 0] }) }).addTo(streetLabelLayer);
    occupied.push(box);
    names.add(candidate.name);
  }
}

function prepareStreetLabels(features) {
  // OSM splits one named street into many short ways. Rejoin connected ways for labels.
  const groups = new Map(), result = [];
  const key = point => point.join(",");
  for (const feature of features) {
    const group = `${feature.properties.highway}|${feature.properties.name}`;
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(feature);
  }
  for (const group of groups.values()) {
    const ends = new Map(), used = new Set();
    group.forEach((feature, index) => {
      const points = feature.geometry.coordinates;
      for (const endpoint of [points[0], points[points.length - 1]]) {
        const id = key(endpoint);
        if (!ends.has(id)) ends.set(id, []);
        ends.get(id).push(index);
      }
    });
    group.forEach((feature, index) => {
      if (used.has(index)) return;
      used.add(index);
      const points = [...feature.geometry.coordinates];
      const grow = () => {
        for (;;) {
          const endpoint = key(points[points.length - 1]);
          const next = (ends.get(endpoint) || []).find(i => !used.has(i));
          if (next === undefined) break;
          used.add(next);
          const coordinates = group[next].geometry.coordinates;
          const extension = key(coordinates[0]) === endpoint ? coordinates : [...coordinates].reverse();
          points.push(...extension.slice(1));
        }
      };
      grow(); points.reverse(); grow(); points.reverse();
      result.push({ ...feature, geometry: { type: "LineString", coordinates: points } });
    });
  }
  return result;
}
