/**
 * Backend-owned catalogues in backend/documents/. They are indexed for the
 * assistant and also used as graph extraction evidence for standards,
 * certifications, tests, and related typed entities.
 */
export const REFERENCE_FILES = ['standards.pdf', 'certifications_tests.pdf'] as const

export const REFERENCE_TOOLTIP =
  'Catalogues from backend/documents. Used for graph extraction and the assistant.'

export function isReferenceFile(name: string): boolean {
  return (REFERENCE_FILES as readonly string[]).includes(name)
}
