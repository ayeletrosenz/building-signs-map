"use strict";
const e$ = id => document.getElementById(id);
const draftKey = `signs-editor-draft:${location.pathname}`;
let editorState, editorDirty=false, editorSaving=false, editorSelection=new Set(), activeLocation=null;
let previewPin=null, previousMapView=null, addressRequest=0;
let tagSuggestions=[],activeTagSuggestion=0;
const tagBucket="tags";

function editorMessage(message) { e$("editor-message").textContent=message; e$("editor-message").hidden=!message; }
function editorPayload() {
  return {revision:editorState.revision,categories:{...editorState.config.categories,[tagBucket]:"תגיות"},
    categoryValues:{...editorState.config.categoryValues,[tagBucket]:getTagCatalog()},annotations:editorState.annotations};
}
function markEditorDirty() {
  editorDirty=true; e$("save-state").textContent="יש שינויים שלא נשמרו";e$("save-editor").disabled=false;
  try {localStorage.setItem(draftKey,JSON.stringify(editorPayload()));}
  catch {editorMessage("לא ניתן לשמור טיוטת גיבוי בדפדפן. יש לשמור לקבצים לפני סגירת הדף.");}
}
async function editorAPI(url, options={}) {
  const response=await fetch(url,{cache:"no-store",...options});
  const data=await response.json();
  if(!response.ok) {const error=new Error(data.error||"הבקשה נכשלה.");error.status=response.status;throw error;}
  return data;
}
async function initEditor() {
  if(!["127.0.0.1","localhost"].includes(location.hostname)) return;
  try {
    await editorAPI("/api/editor");editorAvailable=true;e$("editor-button").hidden=false;
    if(e$("lightbox").open)e$("edit-viewer-photo").hidden=false;
    map.on("click",event=>{if(document.body.classList.contains("locating"))previewEditorPoint(event.latlng);});
  } catch { /* Static hosting stays read-only. */ }
}
function recoverDraft(state) {
  try {
    const draft=JSON.parse(localStorage.getItem(draftKey)||"null");
    if(!draft) return;
    if(draft.revision===state.revision) {
      state.config.categories=draft.categories;state.config.categoryValues=draft.categoryValues;
      state.annotations=draft.annotations;editorDirty=true;editorMessage("טיוטה קודמת שוחזרה. יש ללחוץ על שמירה כדי לעדכן את הקבצים.");
    } else {
      localStorage.setItem(draftKey+":recovery",JSON.stringify(draft));
      editorMessage("נמצאה טיוטה מקבצים ישנים. הקבצים העדכניים נטענו; הטיוטה הקודמת זמינה להורדה.");
      e$("download-draft").hidden=false;
    }
  } catch {editorMessage("לא ניתן לשחזר את טיוטת הדפדפן. הקבצים המקוריים נטענו.");}
}
async function openEditor(reload=false) {
  try {
    const state=await editorAPI("/api/editor");
    if(previousMapView)leaveLocationMode();
    editorMessage("");editorDirty=false;
    if(reload) {
      if(editorState) {
        try {localStorage.setItem(draftKey+":recovery",JSON.stringify(editorPayload()));localStorage.removeItem(draftKey);}catch{}
        e$("download-draft").hidden=false;
      }
      editorMessage("הקבצים העדכניים נטענו. הטיוטה הקודמת זמינה להורדה.");
    } else recoverDraft(state);
    editorState=state;editorSelection.clear();activeLocation=null;e$("location-editor").hidden=true;
    e$("reload-editor").hidden=true;e$("save-state").textContent=editorDirty?"יש שינויים שלא נשמרו":"כל השינויים נשמרו";
    e$("save-editor").disabled=!editorDirty;
    renderEditor();closeMenu();e$("archive-editor").hidden=false;document.body.classList.add("editing");
    e$("map").inert=true;e$("close-editor").focus();
    return true;
  } catch(error) {status(error.message||"לא ניתן לפתוח את העורך. יש להפעיל את scripts/serve.py.");}
}
async function openEditorForPhoto(id) {
  // An open editor may contain unsaved tags/addresses; keep that draft intact.
  if(e$("archive-editor").hidden&&!await openEditor())return;
  if(previousMapView)leaveLocationMode();
  editorSelection=new Set([id]);updateSelection();selectLocation(id);
  e$("lightbox").close();
  const card=[...e$("photo-grid").children].find(item=>item.dataset.photoId===id);
  card?.scrollIntoView({block:"nearest"});
  e$("tag-input").focus({preventScroll:true});
}
function closeEditor() {
  if(editorSaving)return;
  if(editorDirty) {
    e$("close-editor-error").textContent="";
    if(!e$("close-editor-dialog").open)e$("close-editor-dialog").showModal();
    return;
  }
  finishClosingEditor();
}
function finishClosingEditor() {
  addressRequest++;activeLocation=null;
  leaveLocationMode();e$("archive-editor").hidden=true;document.body.classList.remove("editing");e$("map").inert=false;
  e$("menu-toggle").focus();
}
async function saveAndCloseEditor() {
  if(editorSaving)return;
  const dialog=e$("close-editor-dialog");
  for(const button of dialog.querySelectorAll("button"))button.disabled=true;
  e$("close-editor-error").textContent="שומר…";
  try {
    if(await saveEditor()) {dialog.close();finishClosingEditor();}
    else e$("close-editor-error").textContent="השמירה נכשלה. השינויים נשארו בעורך. אפשר לנסות שוב או לחזור לעריכה.";
  } finally {
    for(const button of dialog.querySelectorAll("button"))button.disabled=false;
  }
}
function discardAndCloseEditor() {
  if(editorSaving)return;
  try {localStorage.removeItem(draftKey);}
  catch {
    e$("close-editor-error").textContent="לא ניתן למחוק את טיוטת הדפדפן. השינויים נשארו בעורך.";return;
  }
  editorDirty=false;editorSelection.clear();
  e$("close-editor-dialog").close();finishClosingEditor();editorState=undefined;
}
function annotation(id) {return editorState.annotations[id] ||= {coordinates:null,tags:{}};}
function renderEditor() {renderTagLibrary();renderPhotoGrid();updateSelection();}
function renderPhotoGrid() {
  const grid=e$("photo-grid");grid.replaceChildren();
  for(const photo of editorState.photos) {
    const card=document.createElement("article");card.className="edit-card";card.dataset.photoId=photo.id;
    const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.checked=editorSelection.has(photo.id);
    checkbox.setAttribute("aria-label",`בחירת ${photo.filename}`);
    checkbox.addEventListener("change",()=>{if(checkbox.checked)editorSelection.add(photo.id);else editorSelection.delete(photo.id);updateSelection();});
    const preview=document.createElement("button");preview.className="card-preview";preview.setAttribute("aria-label",`הגדלת ${photo.filename}`);
    const image=document.createElement("img");image.src=photo.thumbnail;image.alt="";image.loading="lazy";preview.append(image);
    preview.addEventListener("click",()=>openPhoto(photo,true));
    const filename=document.createElement("p");filename.className="card-filename";filename.dir="ltr";filename.textContent=photo.filename;
    const tags=document.createElement("div");tags.className="card-tags tag-chip-list";
    const labels=flatTags(annotation(photo.id).tags);
    if(!labels.length)tags.textContent="ללא תגיות";
    for(const label of labels){const chip=document.createElement("span");chip.className="tag-chip card-tag";chip.textContent=label;tags.append(chip);}
    const locate=document.createElement("button");locate.className="card-location";locate.textContent="כתובת ומיקום";
    locate.addEventListener("click",()=>selectLocation(photo.id));
    card.append(checkbox,preview,filename,tags,locate);grid.append(card);
  }
}
function updateSelection() {
  e$("selected-count").textContent=editorSelection.size?`${editorSelection.size} שלטים נבחרו`:"לא נבחרו שלטים";
  for(const card of e$("photo-grid").children) {const checked=editorSelection.has(card.dataset.photoId);card.classList.toggle("selected",checked);card.querySelector('input[type="checkbox"]').checked=checked;}
  renderSelectionTags();
}
function getTagCatalog() {
  const available=Object.values(editorState.config.categoryValues||{}).flat();
  const assigned=Object.values(editorState.annotations).flatMap(entry=>flatTags(entry.tags));
  return flatTags([...available,...assigned]).sort((a,b)=>a.localeCompare(b,"he"));
}
function storeTagCatalog(tags) {
  // One storage bucket keeps the flat UI compatible with the existing local save API.
  editorState.config.categories ||= {};
  editorState.config.categories[tagBucket]="תגיות";
  editorState.config.categoryValues ||= {};
  editorState.config.categoryValues[tagBucket]=flatTags(tags);
}
function setPhotoTags(id,tags) {
  const entry=annotation(id);
  const legacy=Array.isArray(entry.tags)?{}:entry.tags||{};
  entry.tags=Object.fromEntries(Object.keys(legacy).map(key=>[key,[]]));
  entry.tags[tagBucket]=flatTags(tags);
}
function renderTagLibrary() {
  const tags=getTagCatalog();e$("tag-library").replaceChildren();e$("tag-library-empty").hidden=!!tags.length;
  for(const tag of tags) {
    const button=document.createElement("button");button.className="tag-chip";button.textContent=tag;
    button.setAttribute("aria-label",`בחירת השלטים עם התגית ${tag}`);
    button.addEventListener("click",()=>selectSignsWithTag(tag));e$("tag-library").append(button);
  }
}
function selectSignsWithTag(tag) {
  const key=tagKey(tag);
  editorSelection=new Set(editorState.photos.filter(photo=>flatTags(annotation(photo.id).tags).some(value=>tagKey(value)===key)).map(photo=>photo.id));
  hideTagSuggestions();updateSelection();
  e$("tag-status").textContent=editorSelection.size?`${editorSelection.size} שלטים עם התגית ״${tag}״ נבחרו.`:`לתגית ״${tag}״ עדיין אין שלטים.`;
}
function renderSelectionTags() {
  e$("selection-tags-section").hidden=!editorSelection.size;e$("selection-tags").replaceChildren();
  if(!editorSelection.size)return;
  const counts=new Map();
  for(const id of editorSelection)for(const tag of flatTags(annotation(id).tags)) {
    const key=tagKey(tag),previous=counts.get(key);counts.set(key,{tag:previous?.tag||tag,count:(previous?.count||0)+1});
  }
  if(!counts.size){const p=document.createElement("p");p.className="muted";p.textContent="עדיין אין תגיות בשלטים שנבחרו.";e$("selection-tags").append(p);}
  for(const {tag,count} of [...counts.values()].sort((a,b)=>a.tag.localeCompare(b.tag,"he"))) {
    const chip=document.createElement("span");chip.className="tag-chip selection-tag"+(count===editorSelection.size?" is-common":" is-partial");
    const label=document.createElement("span");label.textContent=tag+(count<editorSelection.size?` · ${count}/${editorSelection.size}`:"");
    const remove=document.createElement("button");remove.className="remove-tag";remove.textContent="×";remove.setAttribute("aria-label",`הסרת התגית ${tag}`);
    remove.addEventListener("click",()=>removeTag(tag));chip.append(label,remove);e$("selection-tags").append(chip);
  }
}
function applyTag(raw) {
  const label=String(raw).normalize("NFC").trim().replace(/\s+/gu," ");
  if(!label||label.length>100){e$("tag-status").textContent="יש להזין תגית באורך של עד 100 תווים.";return;}
  const catalog=getTagCatalog(),existing=catalog.find(tag=>tagKey(tag)===tagKey(label)),tag=existing||label;
  if(!existing&&catalog.length>=250){e$("tag-status").textContent="ניתן ליצור עד 250 תגיות.";return;}
  if(!editorSelection.size&&existing){e$("tag-status").textContent="יש לבחור שלטים בגלריה כדי להוסיף להם את התגית.";return;}
  storeTagCatalog([...catalog,tag]);
  for(const id of editorSelection) {
    const tags=flatTags(annotation(id).tags);
    if(!tags.some(value=>tagKey(value)===tagKey(tag)))tags.push(tag);
    setPhotoTags(id,tags);
  }
  e$("tag-input").value="";hideTagSuggestions();markEditorDirty();renderEditor();
  e$("tag-status").textContent=editorSelection.size?`התגית נוספה ל־${editorSelection.size} שלטים. יש ללחוץ על שמירה.`:"התגית נוצרה. בחרו שלטים והוסיפו אותה דרך שדה התגיות.";
}
function removeTag(tag) {
  storeTagCatalog(getTagCatalog());
  for(const id of editorSelection)setPhotoTags(id,flatTags(annotation(id).tags).filter(value=>tagKey(value)!==tagKey(tag)));
  markEditorDirty();renderEditor();e$("tag-status").textContent="התגית הוסרה מהשלטים שנבחרו. יש ללחוץ על שמירה.";
}
function hideTagSuggestions() {
  e$("tag-suggestions").hidden=true;e$("tag-input").setAttribute("aria-expanded","false");e$("tag-input").removeAttribute("aria-activedescendant");
}
function highlightTagSuggestion() {
  for(const [index,button] of [...e$("tag-suggestions").children].entries()) {
    const active=index===activeTagSuggestion;button.setAttribute("aria-selected",String(active));button.classList.toggle("active",active);
  }
  if(tagSuggestions.length)e$("tag-input").setAttribute("aria-activedescendant",`tag-suggestion-${activeTagSuggestion}`);
}
function renderTagSuggestions() {
  const query=e$("tag-input").value.normalize("NFC").trim().replace(/\s+/gu," "),key=tagKey(query),catalog=getTagCatalog();
  const matches=catalog.filter(tag=>tagKey(tag).includes(key)).sort((a,b)=>Number(tagKey(b).startsWith(key))-Number(tagKey(a).startsWith(key))||a.localeCompare(b,"he")).slice(0,8);
  tagSuggestions=matches.map(tag=>({tag,create:false}));
  if(query&&!catalog.some(tag=>tagKey(tag)===key))tagSuggestions.push({tag:query,create:true});
  activeTagSuggestion=0;e$("tag-suggestions").replaceChildren();
  if(!tagSuggestions.length){hideTagSuggestions();return;}
  tagSuggestions.forEach((item,index)=>{
    const button=document.createElement("button");button.type="button";button.tabIndex=-1;button.id=`tag-suggestion-${index}`;button.setAttribute("role","option");
    button.textContent=item.create?`+ יצירת תגית ״${item.tag}״`:item.tag;
    button.addEventListener("mousedown",event=>event.preventDefault());button.addEventListener("click",()=>applyTag(item.tag));
    e$("tag-suggestions").append(button);
  });
  e$("tag-suggestions").hidden=false;e$("tag-input").setAttribute("aria-expanded","true");highlightTagSuggestion();
}
async function saveEditor() {
  if(editorSaving)return false;
  if(!editorDirty)return true;
  editorSaving=true;e$("save-state").textContent="שומר…";e$("archive-editor").inert=true;e$("save-editor").disabled=true;editorMessage("");
  try {
    const saved=await editorAPI("/api/editor",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(editorPayload())});
    editorState=saved;editorDirty=false;
    try {localStorage.removeItem(draftKey);}catch{}
    applyArchive(saved);renderEditor();e$("save-state").textContent="כל השינויים נשמרו בקבצים";
    return true;
  } catch(error) {
    e$("save-state").textContent="השמירה נכשלה — הטיוטה נשמרה";editorMessage(error.message);
    if(error.status===409)e$("reload-editor").hidden=false;
    e$("save-editor").disabled=false;
    return false;
  } finally {editorSaving=false;e$("archive-editor").inert=false;}
}
function selectLocation(id) {
  addressRequest++;activeLocation=id;e$("location-editor").hidden=false;
  const photo=editorState.photos.find(p=>p.id===id),entry=annotation(id);
  e$("location-photo").src=photo.thumbnail;e$("location-filename").textContent=photo.filename;
  e$("edit-address").value=entry.address||"";
  const point=validCoordinates(entry.coordinates)?entry.coordinates:photo.exifCoordinates||photo.coordinates;
  e$("edit-latitude").value=point?.[0]??"";e$("edit-longitude").value=point?.[1]??"";
  e$("address-results").replaceChildren();e$("address-state").textContent="";e$("search-address").disabled=false;
  e$("location-editor").scrollIntoView({block:"nearest"});
}
function enterLocationMode() {
  if(!activeLocation)return;
  if(!previousMapView)previousMapView={center:map.getCenter(),zoom:map.getZoom()};
  document.body.classList.add("locating");e$("map").inert=false;e$("back-gallery").hidden=false;
}
function previewEditorPoint(point) {
  if(!activeLocation)return;
  enterLocationMode();const latlng=L.latLng(point);
  const bounds=L.latLngBounds(map.options.maxBounds.getSouthWest(),map.options.maxBounds.getNorthEast()).extend(latlng);
  map.setMaxBounds(bounds.pad(.1));
  if(!previewPin) {
    const icon=L.divIcon({className:"editor-pin",html:'<span aria-hidden="true"></span>',iconSize:[26,26],iconAnchor:[13,13]});
    previewPin=L.marker(latlng,{draggable:true,icon,alt:"גרירת המיקום המוצע",zIndexOffset:2000}).addTo(map);
    previewPin.on("dragend",()=>setLocationFields(previewPin.getLatLng()));
  } else previewPin.setLatLng(latlng);
  setLocationFields(latlng);
}
function setLocationFields(point) {e$("edit-latitude").value=point.lat.toFixed(7);e$("edit-longitude").value=point.lng.toFixed(7);}
function leaveLocationMode() {
  document.body.classList.remove("locating");e$("back-gallery").hidden=true;
  if(previewPin){map.removeLayer(previewPin);previewPin=null;}
  if(previousMapView) {updateMapLimits();map.setView(previousMapView.center,previousMapView.zoom,{animate:false});previousMapView=null;}
  e$("map").inert=!e$("archive-editor").hidden;
}
async function searchAddress() {
  const address=e$("edit-address").value.trim();if(!address){e$("address-state").textContent="יש להזין כתובת מלאה.";return;}
  const request=++addressRequest,id=activeLocation;e$("search-address").disabled=true;e$("address-state").textContent="מחפש כתובת…";
  e$("address-results").replaceChildren();
  try {
    const result=await editorAPI("/api/geocode?address="+encodeURIComponent(address));
    if(request!==addressRequest||id!==activeLocation)return;
    e$("address-state").textContent=result.results.length?"יש לבחור תוצאה ולבדוק את המיקום לפני האישור.":"לא נמצאה כתובת. אפשר לבחור מיקום ידנית במפה.";
    for(const match of result.results) {
      const button=document.createElement("button");button.className="address-result";button.textContent=match.label;
      button.addEventListener("click",()=>{previewEditorPoint(match.coordinates);map.setView(match.coordinates,18,{animate:false});});
      e$("address-results").append(button);
    }
  } catch(error) {if(request===addressRequest)e$("address-state").textContent=error.message;}
  finally {if(request===addressRequest)e$("search-address").disabled=false;}
}

e$("editor-button").addEventListener("click",()=>openEditor());
e$("close-editor").addEventListener("click",closeEditor);
e$("save-and-close-editor").addEventListener("click",saveAndCloseEditor);
e$("discard-editor-changes").addEventListener("click",discardAndCloseEditor);
e$("cancel-close-editor").addEventListener("click",()=>e$("close-editor-dialog").close());
e$("keep-editing").addEventListener("click",()=>e$("close-editor-dialog").close());
e$("close-editor-dialog").addEventListener("cancel",event=>{if(editorSaving)event.preventDefault();});
e$("save-editor").addEventListener("click",saveEditor);
e$("reload-editor").addEventListener("click",()=>openEditor(true));
e$("download-draft").addEventListener("click",()=>{
  let data;try{data=localStorage.getItem(draftKey+":recovery");}catch{return;}
  if(!data)return;const url=URL.createObjectURL(new Blob([data],{type:"application/json"}));
  const link=document.createElement("a");link.href=url;link.download="archive-editor-draft.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
e$("select-all").addEventListener("click",()=>{editorSelection=new Set(editorState.photos.map(p=>p.id));updateSelection();});
e$("select-none").addEventListener("click",()=>{editorSelection.clear();updateSelection();});
e$("tag-input").addEventListener("input",renderTagSuggestions);
e$("tag-input").addEventListener("focus",renderTagSuggestions);
e$("tag-input").addEventListener("blur",()=>setTimeout(()=>{if(!e$("tag-composer").contains(document.activeElement))hideTagSuggestions();},0));
e$("tag-input").addEventListener("keydown",event=>{
  if(event.key==="Escape"){hideTagSuggestions();event.stopPropagation();return;}
  if(["ArrowDown","ArrowUp"].includes(event.key)) {
    event.preventDefault();
    if(e$("tag-suggestions").hidden){renderTagSuggestions();return;}
    if(tagSuggestions.length){activeTagSuggestion=(activeTagSuggestion+(event.key==="ArrowDown"?1:-1)+tagSuggestions.length)%tagSuggestions.length;highlightTagSuggestion();e$("tag-suggestions").children[activeTagSuggestion]?.scrollIntoView({block:"nearest"});}
  }
  if(event.key==="Enter") {
    event.preventDefault();
    if(e$("tag-suggestions").hidden){if(!e$("tag-input").value.trim())return;renderTagSuggestions();}
    const suggestion=tagSuggestions[activeTagSuggestion];if(suggestion)applyTag(suggestion.tag);
  }
});
e$("search-address").addEventListener("click",searchAddress);
e$("edit-address").addEventListener("input",()=>{
  addressRequest++;e$("search-address").disabled=false;e$("address-results").replaceChildren();e$("address-state").textContent="";
});
e$("choose-map-point").addEventListener("click",()=>{
  enterLocationMode();const point=[Number(e$("edit-latitude").value),Number(e$("edit-longitude").value)];
  previewEditorPoint(e$("edit-latitude").value&&e$("edit-longitude").value&&validCoordinates(point)?point:map.getCenter());
  map.panTo(previewPin.getLatLng(),{animate:false});
});
e$("confirm-location").addEventListener("click",()=>{
  if(!activeLocation)return;const lat=e$("edit-latitude"),lng=e$("edit-longitude"),point=[Number(lat.value),Number(lng.value)];
  if(!lat.value.trim()||!lng.value.trim()||!validCoordinates(point)){e$("address-state").textContent="יש לבחור מיקום תקין לפני האישור.";return;}
  const entry=annotation(activeLocation);entry.coordinates=point;entry.address=e$("edit-address").value.trim();
  markEditorDirty();leaveLocationMode();e$("address-state").textContent="המיקום אושר לטיוטה. יש ללחוץ על שמירה כדי לעדכן את הקבצים.";
});
e$("cancel-location").addEventListener("click",()=>{const id=activeLocation;leaveLocationMode();if(id)selectLocation(id);});
e$("back-gallery").addEventListener("click",()=>{const id=activeLocation;leaveLocationMode();if(id)selectLocation(id);});
window.addEventListener("beforeunload",event=>{if(editorDirty){event.preventDefault();event.returnValue="";}});
document.addEventListener("keydown",event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="s"&&!e$("archive-editor").hidden){event.preventDefault();if(e$("close-editor-dialog").open)saveAndCloseEditor();else saveEditor();}});
