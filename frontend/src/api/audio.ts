/** Native browser audio source for one safe, DB-backed library track. */
export function previewAudioUrl(trackId: number): string {
  return `/api/tracks/${trackId}/preview-audio`
}
