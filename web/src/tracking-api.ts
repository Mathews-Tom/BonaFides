// SPDX-License-Identifier: MIT OR Apache-2.0
import { Api } from './api';

export const stages = ['preparing', 'applied', 'screening', 'interview', 'offer', 'accepted', 'rejected', 'withdrawn'] as const;
export type Stage = typeof stages[number];
export const stageName = (value: Stage) => value.charAt(0).toUpperCase() + value.slice(1);
export interface Appointment {
  id: string; version: number; kind: 'interview' | 'follow_up'; title: string; starts_at: string; ends_at: string | null;
  local_start: string; local_end: string | null; timezone: string; display_timezone: string; display_start: string; display_end: string | null;
  duration_minutes: number | null; status: 'scheduled' | 'completed' | 'cancelled'; completed_at: string | null; cancelled_at: string | null; created_at: string;
}
export interface Note { id: string; version: number; body: string; created_at: string; updated_at: string }
export interface Contact { id: string; version: number; display_name: string; email: string | null; role_label: string | null; created_at: string }
export interface Attachment { id: string; display_name: string; byte_length: number; sha256: string; media_type: 'application/octet-stream'; created_at: string }
export interface ApplicationSummary {
  id: string; version: number; attempt_no: number; posting_id: string; opportunity_id: string; title: string; company: string;
  current_stage: Stage; archived_at: string | null; created_at: string; updated_at: string; next_action: Appointment | null;
}
export interface ApplicationDetail extends ApplicationSummary {
  current_stage_event_id: string | null; notes: Note[]; contacts: Contact[]; attachments: Attachment[]; appointments: Appointment[];
}
export interface ApplicationPage { items: ApplicationSummary[]; next_cursor: string | null; query_elapsed_ms: number; as_of: string }
export interface StageEvent {
  id: string; from_stage: Stage | null; to_stage: Stage; effective_at: string; recorded_at: string; reason: string;
  correction_of: string | null; sequence: number; affects_current_stage: boolean;
}
export interface StagePage { items: StageEvent[]; next_cursor: number | null }
export interface ApplicationCounts {
  period_start: string; period_end: string; current_by_stage: Record<Stage, number>; archived_by_stage: Record<Stage, number>;
  created_in_period: number; stage_reached_in_period: Record<Stage, number>; query_elapsed_ms: number;
}
export interface AttentionPage {
  items: { application_id: string; title: string; company: string; appointment: Appointment }[]; query_elapsed_ms: number; as_of: string;
}
export interface AppointmentWrite {
  kind: Appointment['kind']; title: string; local_start: string; timezone: string; utc_offset_minutes?: number;
  local_end: string | null; end_utc_offset_minutes?: number;
}
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

export async function attachmentDigest(file: File): Promise<string> {
  // FileReader also supports the authenticated browser's native File implementation.
  const bytes = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error('Attachment could not be read'));
    reader.onabort = () => reject(new DOMException('File read stopped', 'AbortError'));
    reader.readAsArrayBuffer(file);
  });
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

// The only binary tracking write. Files never become an object URL or a browser preview.
export async function uploadAttachment(api: Api, applicationId: string, file: File, version: number, key: string, signal: AbortSignal): Promise<ApplicationDetail> {
  return api.upload<ApplicationDetail>(`/applications/${encodeURIComponent(applicationId)}/attachments?display_name=${encodeURIComponent(file.name)}`, file, 'application/octet-stream', version, key, signal);
}
