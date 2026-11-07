import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { changeFor, editorDefault, editorFromValue } from "@/components/forms/editor";
import { raiseNotice } from "@/lib/notices";
import { keys, sectionsService, useSection as useSectionQuery } from "@/services/services";
import { useUiStore } from "@/stores/ui";
import type { FieldSchema, SaveOutcome, SaveRequest, SectionView } from "@/types/api";

export const VALIDATE_DELAY_MS = 300;

export type Draft = Record<string, unknown>;

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function baseDraft(view: SectionView | undefined): Draft {
  if (view === undefined) return {};
  return Object.fromEntries(view.fields.map((field) => [field.key, editorFromValue(field, view.values[field.key])]));
}

function writable(view: SectionView, field: FieldSchema): boolean {
  return !view.read_only && !field.readonly && view.state[field.key]?.locked !== true;
}

/** The request body for the edits: only what changed, in the form the server reads. */
function payloadOf(view: SectionView, edits: Draft, restored: readonly string[]): SaveRequest {
  const changes: Record<string, unknown> = {};
  for (const field of view.fields) {
    if (!(field.key in edits) || restored.includes(field.key)) continue;
    const sent = changeFor(field, edits[field.key]);
    if (sent !== undefined) changes[field.key] = sent;
  }
  return { version: view.version, changes, restore: [...restored] };
}

/** Saving a section: the answer, and what follows a good one. A refusal is an outcome to show, not a failure. */
export function useSaveSection(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (request: SaveRequest) => sectionsService.save(id, request),
    onSuccess: async (outcome: SaveOutcome) => {
      if (!outcome.ok) return;
      if (outcome.restarts.length > 0) useUiStore.getState().addRestart(outcome.restarts);
      if (!outcome.unchanged) raiseNotice("saved");
      await client.invalidateQueries({ queryKey: keys.section(id) });
    },
  });
}

/**
 * One section as a form: the loaded view, the draft the operator edits, whether it differs from what was
 * loaded, the server's checks (300 ms after the last change) and saving. While there are unsaved edits the
 * version loaded stays the one a save sends, so a change made elsewhere is caught as a conflict, not overwritten.
 */
export function useSection(id: string) {
  const query = useSectionQuery(id);
  const [snapshot, setSnapshot] = useState<SectionView | undefined>(query.data);
  const [edits, setEdits] = useState<Draft>({});
  const [restored, setRestored] = useState<string[]>([]);
  const [saveErrors, setSaveErrors] = useState<{ errors: Record<string, string>; general: string[] }>({ errors: {}, general: [] });
  const [checked, setChecked] = useState<{ errors: Record<string, string>; general: string[] }>({ errors: {}, general: [] });
  const mutation = useSaveSection(id);

  const base = useMemo(() => baseDraft(snapshot), [snapshot]);
  const editKeys = Object.keys(edits).filter((key) => !same(edits[key], base[key]));
  const dirty = editKeys.length > 0 || restored.length > 0;

  // Adopt a newer read of the section only while there is nothing to lose.
  if (query.data !== undefined && query.data !== snapshot && (snapshot === undefined || !dirty)) {
    setSnapshot(query.data);
  }

  const draft = useMemo<Draft>(() => ({ ...base, ...edits }), [base, edits]);

  const payload =
    snapshot === undefined ? null : payloadOf(snapshot, Object.fromEntries(editKeys.map((key) => [key, edits[key]])), restored);
  const payloadKey = JSON.stringify(payload);

  useEffect(() => {
    if (payload === null || !dirty) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      sectionsService
        .validate(id, payload)
        .then((result) => {
          if (!cancelled) setChecked({ errors: result.errors, general: result.general });
        })
        .catch(() => undefined);
    }, VALIDATE_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // The key stands for the payload: a new object with the same content must not restart the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, payloadKey, dirty]);

  const clearAnswers = useCallback((): void => {
    mutation.reset();
    setSaveErrors({ errors: {}, general: [] });
  }, [mutation]);

  const setField = useCallback(
    (key: string, value: unknown): void => {
      clearAnswers();
      setRestored((current) => current.filter((item) => item !== key));
      setEdits((current) => ({ ...current, [key]: value }));
    },
    [clearAnswers],
  );

  const restoreField = useCallback(
    (key: string): void => {
      const field = snapshot?.fields.find((item) => item.key === key);
      if (field === undefined) return;
      clearAnswers();
      setRestored((current) => (current.includes(key) ? current : [...current, key]));
      setEdits((current) => ({ ...current, [key]: editorDefault(field) }));
    },
    [snapshot, clearAnswers],
  );

  /** Restores every writable field that holds a saved or edited value. Returns the keys left alone because the environment locks them. */
  const restoreAll = useCallback((): string[] => {
    if (snapshot === undefined) return [];
    const skipped: string[] = [];
    for (const field of snapshot.fields) {
      if (snapshot.state[field.key]?.locked === true) skipped.push(field.key);
      else if (writable(snapshot, field) && (snapshot.state[field.key]?.origin === "file" || editKeys.includes(field.key))) {
        restoreField(field.key);
      }
    }
    return skipped;
    // editKeys is derived from edits and base.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot, edits, base, restoreField]);

  const discard = useCallback((): void => {
    clearAnswers();
    setEdits({});
    setRestored([]);
    setChecked({ errors: {}, general: [] });
  }, [clearAnswers]);

  const reload = useCallback(async (): Promise<void> => {
    discard();
    await query.refetch();
  }, [discard, query]);

  const save = useCallback(
    async (options: { confirmReplace?: boolean } = {}): Promise<SaveOutcome | undefined> => {
      if (payload === null) return undefined;
      const request: SaveRequest = options.confirmReplace ? { ...payload, confirm_replace: true } : payload;
      const outcome = await mutation.mutateAsync(request);
      if (outcome.ok) {
        setEdits({});
        setRestored([]);
        setChecked({ errors: {}, general: [] });
      } else if (!outcome.conflict && !outcome.needs_confirmation) {
        setSaveErrors({ errors: outcome.errors, general: outcome.general });
      }
      return outcome;
    },
    [payload, mutation],
  );

  const outcome = mutation.data;
  const shown = dirty ? { ...checked.errors, ...saveErrors.errors } : {};
  return {
    query,
    view: snapshot,
    draft,
    dirty,
    restored,
    errors: shown,
    general: dirty ? [...checked.general, ...saveErrors.general] : [],
    setField,
    restoreField,
    restoreAll,
    discard,
    reload,
    save,
    saving: mutation.isPending,
    saved: outcome?.ok === true,
    conflict: outcome?.conflict === true,
    needsConfirmation: outcome?.needs_confirmation === true,
  };
}
