/* Static archive; local editing is enabled only by scripts/serve.py. */
"use strict";
const $ = id => document.getElementById(id);
let map, photoLayer, streets, photos = [], annotations = {}, config;
let viewerPhotos = [], viewerIndex = 0;
let mapPanArea, streetCoverageBounds;
let editorAvailable=false;
let activeTag=null;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const thumbnailScaleKey=`signs-thumbnail-scale:${location.pathname}`;
let thumbnailScale=1;
try {
  const saved=Number(localStorage.getItem(thumbnailScaleKey));
  if(Number.isFinite(saved)&&saved>=.6&&saved<=1.6)thumbnailScale=saved;
} catch { /* Display preferences are optional; project metadata is unaffected. */ }
function getThumbnailScale() {return thumbnailScale;}

function validCoordinates(value) {
  return Array.isArray(value) && value.length === 2 && value.every(Number.isFinite)
    && Math.abs(value[0]) <= 90 && Math.abs(value[1]) <= 180;
}
async function json(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Could not load ${path} (${response.status}).`);
  return response.json();
}
function status(message) { $("status").textContent = message; $("status").hidden = !message; }
function coordinates(photo) { return photo.coordinates; }
function tagKey(value) {return String(value).normalize("NFC").trim().replace(/\s+/gu," ").toLocaleLowerCase();}
function flatTags(value) {
  const values=Array.isArray(value)?value:Object.values(value||{}).flat();
  const unique=new Map();
  for(const tag of values)if(typeof tag==="string"&&tag.trim()) {
    const label=tag.normalize("NFC").trim().replace(/\s+/gu," ");
    if(!unique.has(tagKey(label)))unique.set(tagKey(label),label);
  }
  return [...unique.values()];
}
function matches(photo) {
  return activeTag===null||flatTags(photo.tags).some(tag=>tagKey(tag)===activeTag);
}
function photoIcon(photo, size) {
  // Retain the numeric argument used by older cached layout scripts.
  const dimensions=typeof size==="number"
    ? (typeof photoDisplaySize==="function"?photoDisplaySize(photo,size):{width:size,height:size})
    : size;
  const width=Number.isFinite(dimensions?.width)&&dimensions.width>0?dimensions.width:32;
  const height=Number.isFinite(dimensions?.height)&&dimensions.height>0?dimensions.height:32;
  const frame = document.createElement("div");
  frame.className = "photo-frame";
  frame.style.width = `${width}px`;
  frame.style.height = `${height}px`;
  const image = document.createElement("img");
  image.src = photo.thumbnail; image.alt = ""; image.draggable = false; image.decoding = "async";
  frame.append(image);
  return L.divIcon({ html: frame, className: "photo-marker", iconSize: [width,height], iconAnchor: [width/2,height/2] });
}
function renderMarkers() {
  updateMapLimits();
  const visible = photos.filter(matches), located = visible.filter(p => validCoordinates(coordinates(p)));
  layoutPhotos(located);
  status(!located.length ? (visible.length ? "לשלטים האלה עדיין לא הוגדר מיקום." : "אין שלטים שתואמים לסינון.") : "");
}
function updateMapLimits() {
  const points = photos.map(coordinates).filter(validCoordinates);
  const bounds = points.length ? L.latLngBounds(points) : L.latLngBounds([
    [config.defaultCenter[0]-.004,config.defaultCenter[1]-.004],
    [config.defaultCenter[0]+.004,config.defaultCenter[1]+.004]
  ]);
  if (bounds.getNorth()-bounds.getSouth()<.002 || bounds.getEast()-bounds.getWest()<.002) {
    const center=bounds.getCenter(); bounds.extend([center.lat-.001,center.lng-.001]); bounds.extend([center.lat+.001,center.lng+.001]);
  }
  const overview=bounds.pad(config.areaPadding??.35);
  const extent=map.project(overview.getNorthEast(),0).subtract(map.project(overview.getSouthWest(),0)), size=map.getSize();
  const scale=Math.min(size.x/Math.abs(extent.x),size.y/Math.abs(extent.y));
  map.setMinZoom(Math.max(0,Math.min(config.maxZoom,Math.floor(map.getScaleZoom(scale,0)))));
  mapPanArea=bounds.pad(config.panPadding??.5);
  updateMapPanLimits();
}
function updateMapPanLimits() {
  if(!mapPanArea)return;
  // Leaflet bounds constrain the entire viewport. Add half a viewport so the
  // center can still travel around the neighborhood on wide, zoomed-out maps.
  const zoom=map.getZoom(),half=map.getSize().divideBy(2);
  const lower=map.project(mapPanArea.getNorthWest(),zoom).subtract(half);
  const upper=map.project(mapPanArea.getSouthEast(),zoom).add(half);
  if(streetCoverageBounds) {
    const coverageLower=map.project(streetCoverageBounds.getNorthWest(),zoom);
    const coverageUpper=map.project(streetCoverageBounds.getSouthEast(),zoom);
    lower.x=Math.max(lower.x,coverageLower.x);lower.y=Math.max(lower.y,coverageLower.y);
    upper.x=Math.min(upper.x,coverageUpper.x);upper.y=Math.min(upper.y,coverageUpper.y);
  }
  map.setMaxBounds(L.latLngBounds(map.unproject(lower,zoom),map.unproject(upper,zoom)));
}
function streetStyle(feature) {
  const kind=feature.properties.highway, major=/^(motorway|trunk|primary|secondary|tertiary)/.test(kind);
  const path=["footway","steps","path"].includes(kind);
  return {className:"archive-street",color:"#f2b0be",weight:major?2.5:path?.8:1.35,
    opacity:path?(map.getZoom()>=17?.6:0):kind==="service"?.65:.95,
    lineCap:"round",lineJoin:"round",interactive:false,smoothFactor:1.2};
}
async function addBasemap() {
  if (config.basemap==="diagram") {
    const [data,labels]=await Promise.all([json(config.streetData),json(config.streetLabels)]);
    if(Array.isArray(data.bbox)&&data.bbox.length===4) {
      const [west,south,east,north]=data.bbox;
      streetCoverageBounds=L.latLngBounds([south,west],[north,east]);
      updateMapPanLimits();
    }
    streets=L.geoJSON(data,{style:streetStyle,interactive:false}).addTo(map);
    streetLabelData=prepareStreetLabels(labels.features);
    streetLabelLayer=L.layerGroup().addTo(map);
    map.on("zoomend",()=>streets.setStyle(streetStyle));
    layoutStreetLabels();
  } else {
    L.tileLayer(config.tileUrl,{maxZoom:config.maxZoom,attribution:config.tileAttribution,keepBuffer:2,updateWhenIdle:true}).addTo(map);
  }
  map.attributionControl.addAttribution(config.tileAttribution);
}
function fitPhotos() {
  const points=photos.filter(matches).map(coordinates).filter(validCoordinates);
  if(points.length) map.fitBounds(L.latLngBounds(points),{paddingTopLeft:[55,75],paddingBottomRight:[55,55],maxZoom:18,animate:!reducedMotion});
}
function buildFilters() {
  const reset=$("clear-filters");$("filters").replaceChildren(reset);
  const tags=flatTags(photos.flatMap(photo=>flatTags(photo.tags))).sort((a,b)=>a.localeCompare(b,"he"));
  for(const tag of tags) {
    const key=tagKey(tag),button=document.createElement("button");button.className="filter-option";
    button.textContent=tag;button.dataset.tag=key;button.setAttribute("aria-pressed",String(activeTag===key));
    button.addEventListener("click",()=>{
      activeTag=activeTag===key?null:key;updateFilterSelection();renderMarkers();
    });
    $("filters").append(button);
  }
  $("filters-empty").hidden=!!tags.length;
}
function updateFilterSelection() {
  for(const button of $("filters").querySelectorAll(".filter-option")) {
    const active=button.id==="clear-filters"?activeTag===null:button.dataset.tag===activeTag;
    button.setAttribute("aria-pressed",String(active));
  }
}
function showViewerImage() {
  const photo=viewerPhotos[viewerIndex];
  $("image-error").hidden=true; $("full-image").hidden=false;
  $("full-image").src=photo.image;
  const tags=flatTags(document.body.classList.contains("editing")?annotation(photo.id).tags:photo.tags);
  $("image-tags").replaceChildren();
  if(!tags.length)$("image-tags").textContent="ללא תגיות";
  for(const tag of tags) {
    const label=document.createElement("span");label.className="viewer-tag";label.textContent=tag;
    $("image-tags").append(label);
  }
  $("edit-viewer-photo").hidden=!editorAvailable;
  $("previous-image").hidden=$("next-image").hidden=viewerPhotos.length<2;
}
function openPhoto(photo, single=false) {
  const marker=photoLayer.getLayers().find(m=>m.photo.id===photo.id);
  viewerPhotos=single||!marker?[photo]:photoLayer.getLayers().filter(m=>intersects(marker.photoBox,m.photoBox)).map(m=>m.photo).sort((a,b)=>a.id.localeCompare(b.id));
  if(!viewerPhotos.some(p=>p.id===photo.id)) viewerPhotos=[photo];
  viewerIndex=viewerPhotos.findIndex(p=>p.id===photo.id);
  showViewerImage(); $("lightbox").showModal();
}
function navigateImage(direction) { viewerIndex=(viewerIndex+direction+viewerPhotos.length)%viewerPhotos.length; showViewerImage(); }
$("previous-image").addEventListener("click",()=>navigateImage(-1));
$("next-image").addEventListener("click",()=>navigateImage(1));
$("edit-viewer-photo").addEventListener("click",()=>{if(typeof openEditorForPhoto==="function")openEditorForPhoto(viewerPhotos[viewerIndex].id);});
$("close-lightbox").addEventListener("click",()=>$("lightbox").close());
$("lightbox").addEventListener("click",event=>{
  const r=$("lightbox").getBoundingClientRect();
  if(event.target===$("lightbox")&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)) $("lightbox").close();
});
$("lightbox").addEventListener("close",()=>$("full-image").removeAttribute("src"));
$("full-image").addEventListener("error",()=>{if($("lightbox").open){$("full-image").hidden=true;$("image-error").hidden=false;}});
function closeMenu() {
  const focused=$("menu-drawer").contains(document.activeElement);
  $("menu-drawer").classList.remove("is-open"); $("menu-drawer").inert=true;
  $("menu-toggle").setAttribute("aria-expanded","false");
  if(focused) $("menu-toggle").focus();
}
$("menu-toggle").addEventListener("click",()=>{
  if($("menu-toggle").getAttribute("aria-expanded")==="true") return closeMenu();
  $("menu-drawer").inert=false; $("menu-drawer").classList.add("is-open"); $("menu-toggle").setAttribute("aria-expanded","true");
});
$("close-menu").addEventListener("click",closeMenu);
$("map-zoom").addEventListener("input",()=>{
  if(map)map.setZoom(Number($("map-zoom").value),{animate:false});
});
$("clear-filters").addEventListener("click",()=>{activeTag=null;updateFilterSelection();renderMarkers();});
function updateThumbnailControl() {
  const percent=Math.round(thumbnailScale*100);
  $("thumbnail-size").value=String(percent);
  $("thumbnail-size").setAttribute("aria-valuetext",`${percent} אחוז`);
}
updateThumbnailControl();
$("thumbnail-size").addEventListener("input",()=>{
  thumbnailScale=Math.max(.6,Math.min(1.6,Number($("thumbnail-size").value)/100));
  updateThumbnailControl();
  try {localStorage.setItem(thumbnailScaleKey,String(thumbnailScale));}catch{}
  if(map&&photoLayer)scheduleMapLayout();
});
document.addEventListener("keydown",event=>{
  if($("lightbox").open) {
    if(event.key==="ArrowLeft") {event.preventDefault();navigateImage(1);}
    if(event.key==="ArrowRight") {event.preventDefault();navigateImage(-1);}
  } else if(event.key==="Escape") closeMenu();
});
function applyArchive(state) {
  config=state.config;annotations=state.annotations;photos=state.photos;
  activeTag=null;buildFilters();updateFilterSelection();renderMarkers();
}
async function start() {
  try {
    config=await json("config.json");
    [photos,annotations]=await Promise.all([json("data/photos.json"),config.readOnly?Promise.resolve({}):json("data/annotations.json")]);
    map=L.map("map",{zoomControl:false,maxZoom:config.maxZoom,zoomAnimation:!reducedMotion,maxBoundsViscosity:1,worldCopyJump:false}).setView(config.defaultCenter,config.defaultZoom);
    L.control.scale({position:"bottomleft",imperial:false}).addTo(map);map.attributionControl.setPrefix(false);
    map.createPane("streetLabels").style.zIndex=460;map.getPane("streetLabels").style.pointerEvents="none";
    photoLayer=L.layerGroup().addTo(map);
    const updateZoom=()=>{
      const control=$("map-zoom"),minimum=map.getMinZoom(),maximum=map.getMaxZoom();
      control.min=String(minimum);control.max=String(maximum);control.value=String(Math.round(map.getZoom()));
      control.disabled=minimum===maximum;
      control.setAttribute("aria-valuetext",`רמת קירוב ${Math.round(map.getZoom())-minimum+1} מתוך ${maximum-minimum+1}`);
    };
    map.on("zoom zoomend zoomlevelschange",updateZoom);map.on("moveend resize",scheduleMapLayout);
    map.on("zoomend",updateMapPanLimits);
    buildFilters();renderMarkers();fitPhotos();updateZoom();document.fonts.ready.then(scheduleMapLayout);
    map.on("resize",()=>{const overview=map.getZoom()<=map.getMinZoom();updateMapLimits();if(overview)fitPhotos();});
    try{await addBasemap();}catch(error){console.error(error);status("לא ניתן לטעון את נתוני הרחובות.");}
    if(!config.readOnly&&typeof initEditor==="function")initEditor();
  } catch(error) {console.error(error);status("לא ניתן לטעון את הארכיון. יש להפעיל את השרת המקומי ולבדוק את הנתונים.");}
}
start();
