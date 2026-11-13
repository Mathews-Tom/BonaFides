// SPDX-License-Identifier: MIT OR Apache-2.0
import { useEffect, useRef, useState, type Dispatch, type KeyboardEvent, type PointerEvent, type SetStateAction } from 'react';
import type { PortraitPin, Profile, ProfileIdentity, ReferenceContact, Snapshot } from './api';
import './ProfileIdentity.css';

type PortraitDraft = { portrait: PortraitPin | null; portrait_upload: string | null; include_portrait: boolean };
type CropDraft = { url: string; image: HTMLImageElement; zoom: number; x: number; y: number };
export interface IdentityDraft extends Required<ProfileIdentity> {
  portrait_upload: string | null;
  portrait_loading: boolean;
  crop: CropDraft | null;
  removed_portrait: PortraitDraft | null;
  removed_references: { value: ReferenceContact; index: number }[];
}
const emptyReference: ReferenceContact = { name: '', relationship: '', organization: '', email: '', phone: '' };
const referenceFields = [
  ['name', 'Name', 200], ['relationship', 'Relationship', 200], ['organization', 'Organization', 200],
  ['email', 'Email', 320], ['phone', 'Phone', 60],
] as const;
export function identityFrom(snapshot: ProfileIdentity): IdentityDraft {
  return {
    birth_date: snapshot.birth_date ?? null, include_age: snapshot.include_age ?? false,
    portrait: snapshot.portrait ?? null, include_portrait: snapshot.include_portrait ?? true,
    references: snapshot.references ?? [], include_references: snapshot.include_references ?? false,
    portrait_upload: null, portrait_loading: false, crop: null, removed_portrait: null, removed_references: [],
  };
}
function savedValues(value: IdentityDraft): Required<ProfileIdentity> {
  return {
    birth_date: value.birth_date, include_age: value.include_age, portrait: value.portrait,
    include_portrait: value.include_portrait, references: value.references, include_references: value.include_references,
  };
}
export function identityChanged(value: IdentityDraft, snapshot: Snapshot): boolean {
  return value.portrait_loading || value.crop !== null || value.portrait_upload !== null || JSON.stringify(savedValues(value)) !== JSON.stringify(savedValues(identityFrom(snapshot)));
}
export function identityRequest(value: IdentityDraft, snapshot: Snapshot): ProfileIdentity & { portrait_upload?: string } {
  const defaults = savedValues(identityFrom({}));
  const result = Object.fromEntries(Object.entries(savedValues(value)).filter(([key, field]) =>
    key in snapshot || JSON.stringify(field) !== JSON.stringify(defaults[key as keyof ProfileIdentity]),
  ));
  return { ...result, ...(value.portrait_upload ? { portrait_upload: value.portrait_upload } : {}) };
}
export function IdentitySummary({ snapshot }: { snapshot: Snapshot }) {
  return <div><p>Birth date: {snapshot.birth_date || 'Not supplied'}; print age: {snapshot.include_age ? 'yes' : 'no'}.</p>
    <p>Portrait: {snapshot.portrait ? 'saved' : 'none'}; print portrait: {(snapshot.include_portrait ?? true) ? 'yes' : 'no'}.</p>
    {snapshot.portrait && <img className="portrait-preview" alt="Compared saved portrait" src={`/api/v1/profile/portrait/${encodeURIComponent(snapshot.portrait.blob_id)}`} />}
    <p>Print references: {snapshot.include_references ? 'yes' : 'no'}.</p>
    <ul>{(snapshot.references ?? []).map((item, index) => <li key={index}>{referenceFields.map(([key]) => item[key]).filter(Boolean).join(' · ') || 'Blank reference'}</li>)}</ul></div>;
}
export function ProfilePresence({ profile, name, headline, description }: { profile: Profile; name: string; headline: string; description: string }) {
  const selected = profile.current_revision.claims.filter(item => !item.archived && profile.current_revision.snapshot.claim_revision_ids.includes(item.revision.id));
  const areas = [
    { label: 'Display name', present: !!name.trim() },
    { label: 'Headline or description', present: !!(headline.trim() || description.trim()) },
    { label: 'Work / experience', present: selected.some(item => item.kind === 'experience') },
    { label: 'Education', present: selected.some(item => item.kind === 'education') },
    { label: 'Skills', present: selected.some(item => item.kind === 'skill') },
  ];
  return <section className="profile-presence" aria-label="Profile material presence"><h3>{areas.filter(item => item.present).length} of 5 areas present in this editor</h3>
    <ul>{areas.map(item => <li key={item.label}>{item.label}: {item.present ? 'present' : 'not present'}</li>)}</ul>
    <p className="hint">Presence only; not accuracy or qualifications. Optional contact details, age, portrait, references, publications and writing facts add no points. Unsaved changes are not saved material.</p></section>;
}
function geometry(crop: CropDraft) {
  const scale = Math.max(480 / crop.image.naturalWidth, 640 / crop.image.naturalHeight) * crop.zoom;
  return { scale, limitX: (crop.image.naturalWidth * scale - 480) / 2, limitY: (crop.image.naturalHeight * scale - 640) / 2 };
}
function bounded(crop: CropDraft): CropDraft {
  const { limitX, limitY } = geometry(crop);
  return { ...crop, x: Math.max(-limitX, Math.min(limitX, crop.x)), y: Math.max(-limitY, Math.min(limitY, crop.y)) };
}
export function IdentityEditor({ value, onChange }: { value: IdentityDraft; onChange: Dispatch<SetStateAction<IdentityDraft>> }) {
  const [error, setError] = useState('');
  const drag = useRef<{ pointer: number; x: number; y: number; startX: number; startY: number } | null>(null);
  const generation = useRef(0);
  const loadingUrl = useRef<string | null>(null);
  const crop = value.crop;
  useEffect(() => () => { if (crop?.url) URL.revokeObjectURL(crop.url); }, [crop?.url]);
  useEffect(() => () => {
    generation.current++;
    if (loadingUrl.current) URL.revokeObjectURL(loadingUrl.current);
  }, []);
  function change(update: Partial<IdentityDraft>) { onChange(current => ({ ...current, ...update })); }
  function choose(file: File | undefined) {
    if (!file) return;
    change({ portrait_loading: true });
    const current = ++generation.current;
    if (loadingUrl.current) URL.revokeObjectURL(loadingUrl.current);
    const url = URL.createObjectURL(file);
    loadingUrl.current = url;
    const image = new Image();
    image.onload = () => {
      if (current !== generation.current) return;
      loadingUrl.current = null;
      if (!image.naturalWidth || !image.naturalHeight) { URL.revokeObjectURL(url); change({ portrait_loading: false }); setError('This image has no usable dimensions. Choose another image; the prior portrait is unchanged.'); return; }
      setError(''); change({ portrait_loading: false, crop: { url, image, zoom: 1, x: 0, y: 0 } });
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      if (current !== generation.current) return;
      loadingUrl.current = null;
      change({ portrait_loading: false });
      setError('This image could not be decoded. Choose another image; the prior portrait is unchanged.');
    };
    image.src = url;
  }
  function cancelCrop() {
    generation.current++;
    if (loadingUrl.current) URL.revokeObjectURL(loadingUrl.current);
    loadingUrl.current = null; change({ portrait_loading: false, crop: null }); setError('');
  }
  function confirmCrop() {
    if (!crop || value.portrait_loading) return;
    try {
      const canvas = document.createElement('canvas'); canvas.width = 480; canvas.height = 640;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Canvas unavailable');
      const { scale } = geometry(crop);
      context.drawImage(crop.image, (480 - crop.image.naturalWidth * scale) / 2 + crop.x, (640 - crop.image.naturalHeight * scale) / 2 + crop.y, crop.image.naturalWidth * scale, crop.image.naturalHeight * scale);
      const encoded = canvas.toDataURL('image/png').split(',')[1];
      if (!encoded || encoded.length > 700000) { setError('The cropped PNG exceeds the 700,000-character upload limit. Choose a simpler image or crop; the prior portrait is unchanged.'); return; }
      change({ portrait: null, portrait_upload: encoded, crop: null, removed_portrait: null });
      setError('');
    } catch { setError('The crop could not be prepared. The prior portrait is unchanged. Try another image.'); }
  }
  function pan(event: PointerEvent<HTMLDivElement>) {
    if (!crop || !drag.current || drag.current.pointer !== event.pointerId) return;
    const ratio = 480 / event.currentTarget.getBoundingClientRect().width;
    change({ crop: bounded({ ...crop, x: drag.current.startX + (event.clientX - drag.current.x) * ratio, y: drag.current.startY + (event.clientY - drag.current.y) * ratio }) });
  }
  function arrow(event: KeyboardEvent<HTMLDivElement>) {
    if (!crop) return;
    const direction: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const delta = direction[event.key];
    if (!delta) return;
    event.preventDefault();
    const step = event.shiftKey ? 40 : 8;
    change({ crop: bounded({ ...crop, x: crop.x + delta[0] * step, y: crop.y + delta[1] * step }) });
  }
  const hasPortrait = !!(value.portrait || value.portrait_upload);
  const size = crop ? geometry(crop) : null;
  return <section className="profile-identity-options" aria-label="Optional printable identity">
    <h3>Optional printable identity</h3>
    <p className="hint">Age, portrait and references print only with identity-enabled (v3) templates. These optional details are excluded from model projections. Existing documents do not change.</p>
    <label>Birth date (optional)<input type="date" value={value.birth_date ?? ''} max={new Date().toLocaleDateString('en-CA')} onChange={event => change({ birth_date: event.target.value || null })} /></label>
    <label className="check"><input type="checkbox" checked={value.include_age} onChange={event => change({ include_age: event.target.checked })} />Print my age in new résumés</label>
    <p className="hint">Turning age off retains your birth date. Only age, not your birth date, is printed; ages outside 0–149 are not printed.</p>
    <section aria-label="Portrait editor"><h4>Portrait</h4>
      {hasPortrait && <img className="portrait-preview" alt="Current portrait draft" src={value.portrait_upload ? `data:image/png;base64,${value.portrait_upload}` : `/api/v1/profile/portrait/${encodeURIComponent(value.portrait!.blob_id)}`} />}
      <label>{hasPortrait ? 'Replace portrait locally' : 'Choose portrait locally'}<input type="file" accept="image/*" onChange={event => { choose(event.target.files?.[0]); event.target.value = ''; }} /></label>
      <p className="hint">The original image stays in your browser. Only the confirmed 480 × 640 PNG is sent, when you explicitly save your profile.</p>
      {value.portrait_loading && <p role="status">Preparing local image… <button type="button" className="secondary" onClick={cancelCrop}>Cancel image selection</button></p>}
      {error && <p className="error" role="alert">{error}</p>}
      {crop && size && <div className="portrait-crop-editor">
        <p id="portrait-crop-help">Drag or focus the image and use arrow keys to move it (Shift moves farther). Use the zoom slider with arrow keys. The round guide is not part of the rectangular PNG.</p>
        <div className="portrait-crop" role="group" aria-label="Portrait crop position" aria-describedby="portrait-crop-help" tabIndex={0} onKeyDown={arrow}
          onPointerDown={event => { if (event.button !== 0) return; event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId); drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, startX: crop.x, startY: crop.y }; }}
          onPointerMove={pan} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
          <img src={crop.url} alt="Local crop preview" draggable={false} style={{ width: `${crop.image.naturalWidth * size.scale / 480 * 100}%`, height: `${crop.image.naturalHeight * size.scale / 640 * 100}%`, left: `${50 + crop.x / 480 * 100}%`, top: `${50 + crop.y / 640 * 100}%` }} />
          <span className="portrait-round-guide" aria-hidden="true" />
        </div>
        <label>Portrait zoom: {crop.zoom.toFixed(2)}×<input type="range" min={1} max={4} step={0.01} value={crop.zoom} onChange={event => change({ crop: bounded({ ...crop, zoom: Number(event.target.value) }) })} /></label>
        <div className="actions"><button type="button" disabled={value.portrait_loading} onClick={confirmCrop}>Use this crop</button><button type="button" className="secondary" onClick={cancelCrop}>Cancel crop</button></div>
      </div>}
      <label className="check"><input type="checkbox" checked={value.include_portrait} onChange={event => change({ include_portrait: event.target.checked })} />Print portrait in new résumés</label>
      {hasPortrait && <button type="button" className="secondary" onClick={() => { cancelCrop(); change({ removed_portrait: { portrait: value.portrait, portrait_upload: value.portrait_upload, include_portrait: value.include_portrait }, portrait: null, portrait_upload: null }); }}>Remove portrait</button>}
      {value.removed_portrait && <button type="button" className="secondary" onClick={() => { cancelCrop(); change({ ...value.removed_portrait!, removed_portrait: null }); }}>Restore removed portrait</button>}
    </section>
    <section aria-label="Reference contacts"><h4>References ({value.references.length} / 20)</h4>
      <label className="check"><input type="checkbox" checked={value.include_references} onChange={event => change({ include_references: event.target.checked })} />Print references in new résumés</label>
      <p className="hint">Off by default. Tailored résumés additionally require your reviewed confirmation that the posting requests references. Printing does not establish permission or contact anyone. Blank fields are allowed.</p>
      {value.references.map((item, index) => <fieldset key={index}><legend>Reference {index + 1}</legend>
        {referenceFields.map(([field, label, limit]) => <label key={field}>{label}<input type={field === 'phone' ? 'tel' : 'text'} maxLength={limit} value={item[field]} onChange={event => change({ references: value.references.map((row, position) => position === index ? { ...row, [field]: event.target.value } : row) })} /></label>)}
        <button type="button" className="secondary" onClick={() => change({ references: value.references.filter((_, position) => position !== index), removed_references: [...value.removed_references, { value: item, index }] })}>Remove reference {index + 1}</button>
      </fieldset>)}
      <button type="button" className="secondary" disabled={value.references.length >= 20} onClick={() => change({ references: [...value.references, { ...emptyReference }] })}>Add reference</button>
      {value.removed_references.map((removed, index) => <p key={index}><button type="button" className="secondary" disabled={value.references.length >= 20} onClick={() => { const references = [...value.references]; references.splice(Math.min(removed.index, references.length), 0, removed.value); change({ references, removed_references: value.removed_references.filter((_, position) => position !== index) }); }}>Restore removed reference: {removed.value.name || 'Unnamed reference'}</button></p>)}
    </section>
  </section>;
}
