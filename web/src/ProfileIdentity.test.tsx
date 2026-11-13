// SPDX-License-Identifier: MIT OR Apache-2.0
import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { IdentityEditor, identityFrom, identityRequest } from './ProfileIdentity';
import type { Snapshot } from './api';

const snapshot: Snapshot = { display_name: 'Synthetic owner', headline: '', claim_revision_ids: [], portrait: { blob_id: 'portrait-one', sha256: 'a'.repeat(64), byte_length: 100 }, include_portrait: false };
function Harness({ submit, initial = snapshot }: { submit: (body: unknown) => void; initial?: Snapshot }) {
  const [value, setValue] = useState(() => identityFrom(initial));
  return <><IdentityEditor value={value} onChange={setValue} /><button onClick={() => submit(identityRequest(value, initial))}>Explicit profile save</button></>;
}
function imageMocks() {
  const revoke = vi.fn();
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:local-original');
    static revokeObjectURL = revoke;
  });
  vi.stubGlobal('Image', class {
    naturalWidth = 960; naturalHeight = 640;
    onload: (() => void) | null = null;
    set src(_value: string) { queueMicrotask(() => this.onload?.()); }
  });
  const draw = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: draw } as unknown as CanvasRenderingContext2D);
  const encode = vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,Y3JvcA==');
  return { draw, encode, revoke };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('local identity drafts', () => {
  it('bounds keyboard crop movement, retains hidden replacement, and uploads only the confirmed PNG at explicit save', async () => {
    const { draw, revoke } = imageMocks(); const submit = vi.fn();
    render(<Harness submit={submit} />);
    fireEvent.change(screen.getByLabelText('Replace portrait locally'), { target: { files: [new File(['original-private-image'], 'portrait.jpg', { type: 'image/jpeg' })] } });
    const position = await screen.findByRole('group', { name: 'Portrait crop position' });
    for (let index = 0; index < 100; index++) fireEvent.keyDown(position, { key: 'ArrowRight', shiftKey: true });
    fireEvent.click(screen.getByRole('button', { name: 'Use this crop' }));
    expect(draw.mock.calls[0]?.slice(1)).toEqual([0, 0, 960, 640]);
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Print portrait in new résumés')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Remove portrait' }));
    fireEvent.click(screen.getByRole('button', { name: 'Restore removed portrait' }));
    fireEvent.click(screen.getByRole('button', { name: 'Explicit profile save' }));
    expect(submit).toHaveBeenLastCalledWith({ portrait: null, include_portrait: false, portrait_upload: 'Y3JvcA==' });
    expect(revoke).toHaveBeenCalledWith('blob:local-original');
  });
  it('does not turn printing back on when a first portrait is cropped after explicit opt-out', async () => {
    imageMocks();
    render(<Harness submit={vi.fn()} initial={{ ...snapshot, portrait: null, include_portrait: true }} />);
    fireEvent.click(screen.getByLabelText('Print portrait in new résumés'));
    fireEvent.change(screen.getByLabelText('Choose portrait locally'), { target: { files: [new File(['local'], 'portrait.png', { type: 'image/png' })] } });
    await screen.findByRole('group', { name: 'Portrait crop position' });
    fireEvent.click(screen.getByRole('button', { name: 'Use this crop' }));
    expect(screen.getByLabelText('Print portrait in new résumés')).not.toBeChecked();
  });
  it('cancel and oversized PNG never replace the saved pin or its visibility', async () => {
    const { encode } = imageMocks(); const submit = vi.fn();
    render(<Harness submit={submit} />);
    const choose = () => fireEvent.change(screen.getByLabelText('Replace portrait locally'), { target: { files: [new File(['local'], 'portrait.png', { type: 'image/png' })] } });
    choose(); await screen.findByRole('group', { name: 'Portrait crop position' });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel crop' }));
    fireEvent.click(screen.getByRole('button', { name: 'Explicit profile save' }));
    expect(submit).toHaveBeenLastCalledWith({ portrait: snapshot.portrait, include_portrait: false });
    choose(); await screen.findByRole('group', { name: 'Portrait crop position' });
    encode.mockReturnValue(`data:image/png;base64,${'A'.repeat(700001)}`);
    fireEvent.click(screen.getByRole('button', { name: 'Use this crop' }));
    expect(screen.getByRole('alert')).toHaveTextContent('exceeds');
    fireEvent.click(screen.getByRole('button', { name: 'Explicit profile save' }));
    expect(submit).toHaveBeenLastCalledWith({ portrait: snapshot.portrait, include_portrait: false });
  });
  it('bounds reference additions and restores each locally removed row without changing global printing', async () => {
    const submit = vi.fn(); render(<Harness submit={submit} />);
    for (let index = 0; index < 20; index++) fireEvent.click(screen.getByRole('button', { name: 'Add reference' }));
    expect(screen.getByRole('button', { name: 'Add reference' })).toBeDisabled();
    fireEvent.change(screen.getAllByLabelText('Name')[0]!, { target: { value: 'First reference' } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove reference 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Restore removed reference: First reference' }));
    await waitFor(() => expect(screen.getAllByLabelText('Name')[0]).toHaveValue('First reference'));
    expect(screen.getByLabelText('Print references in new résumés')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Explicit profile save' }));
    expect(submit.mock.calls[0]?.[0].references).toHaveLength(20);
    expect(submit.mock.calls[0]?.[0].portrait_upload).toBeUndefined();
  });
});
