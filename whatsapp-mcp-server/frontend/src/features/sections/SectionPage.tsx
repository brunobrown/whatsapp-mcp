import { useParams } from "@tanstack/react-router";
import { useRef, useState } from "react";
import type { SyntheticEvent } from "react";
import { Banner, ErrorState, Skeleton } from "@/components/feedback/feedback";
import { useNotify } from "@/components/feedback/toaster";
import { FieldRenderer } from "@/components/forms/FieldRenderer";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useT } from "@/i18n/t";
import type { FieldSchema, SectionView } from "@/types/api";
import { groupFields } from "./groupFields";
import { useLeaveGuard } from "./useLeaveGuard";
import { useSection } from "./useSection";

/** The top-level field a dotted error key belongs to: `profiles.0.targets` belongs to `profiles`. */
function ownerOf(key: string): string {
  return key.split(".")[0] ?? key;
}

function Loading() {
  const t = useT();
  return (
    <div aria-busy="true" aria-label={t("common.loading")} className="flex flex-col gap-6 py-2">
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="h-11" />
      <Skeleton className="h-5 w-1/2" />
      <Skeleton className="h-11" />
      <Skeleton className="h-24" />
    </div>
  );
}

function Notes({ view }: { view: SectionView }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-3">
      {view.description ? <p className="m-0 text-muted">{view.description}</p> : null}
      {view.restart ? <p className="m-0 text-sm text-muted">{t(`section.restartNote.${view.restart}`)}</p> : null}
      {view.status === "error" && view.banner ? (
        <Banner kind="error" title={t("section.problem.title")}>
          {view.banner}
        </Banner>
      ) : null}
      {view.status === "unavailable" ? (
        <Banner kind="warning" title={t("section.unavailable.title")}>
          {view.banner}
        </Banner>
      ) : null}
      {view.status === "empty" ? <Banner kind="info">{t("section.empty")}</Banner> : null}
      {view.warnings.map((warning) => (
        <Banner key={warning} kind="warning">
          {warning}
        </Banner>
      ))}
    </div>
  );
}

/**
 * One section as a form, built from its schema: whatever fields the server lists are the fields shown.
 * The form is keyed by the section, so moving to another one never carries a draft over.
 */
export function SectionPage() {
  const { sectionId = "" } = useParams({ strict: false });
  return <SectionForm key={sectionId} sectionId={sectionId} />;
}

function SectionForm({ sectionId }: { sectionId: string }) {
  const t = useT();
  const section = useSection(sectionId);
  const guard = useLeaveGuard(section.dirty);
  const { view, draft, errors, general, dirty, query } = section;
  const { requestPermission } = useNotify();
  const form = useRef<HTMLFormElement>(null);
  const [attempted, setAttempted] = useState(false);
  const [askRestore, setAskRestore] = useState(false);
  const [askReplace, setAskReplace] = useState(false);
  const [skipped, setSkipped] = useState<string[]>([]);

  if (view === undefined && query.isError) {
    return (
      <ErrorState
        title={t("section.loadError")}
        message={query.error.message}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (view === undefined) return <Loading />;

  const readOnly = view.read_only;
  const errorKeys = Object.keys(errors);
  const failing = view.fields.filter((field) => errorKeys.some((key) => ownerOf(key) === field.key));
  const showSummary = attempted && (failing.length > 0 || general.length > 0 || errorKeys.length > 0);

  const focusField = (key: string): void => {
    const box = form.current?.querySelector(`[data-field="${CSS.escape(key)}"]`);
    // The control the message is about; failing that, the first place to type, then any button.
    const target =
      box?.querySelector<HTMLElement>('[aria-invalid="true"]') ??
      box?.querySelector<HTMLElement>("input, textarea, [role=switch], [role=combobox], [role=radio]") ??
      box?.querySelector<HTMLElement>("button");
    target?.focus();
  };

  const submit = async (event: SyntheticEvent): Promise<void> => {
    event.preventDefault();
    if (readOnly || !dirty || section.saving) return;
    void requestPermission();
    setAttempted(true);
    setSkipped([]);
    const outcome = await section.save();
    if (outcome === undefined) return;
    if (outcome.needs_confirmation) setAskReplace(true);
    else if (outcome.ok) setAttempted(false);
    else {
      const first = view.fields.find((field) => Object.keys(outcome.errors).some((key) => ownerOf(key) === field.key));
      if (first !== undefined) focusField(first.key);
    }
  };

  const labelOf = (field: FieldSchema): string => field.label;

  return (
    <div className="flex flex-col gap-6">
      <Notes view={view} />

      {section.conflict ? (
        <Banner kind="warning" title={t("section.conflict.title")}>
          <p className="m-0">{t("section.conflict.body")}</p>
          <Button
            variant="secondary"
            className="mt-3"
            onClick={() => {
              setAttempted(false);
              void section.reload();
            }}
          >
            {t("section.conflict.reload")}
          </Button>
        </Banner>
      ) : null}

      {showSummary ? (
        <Banner kind="error" title={failing.length > 0 ? t("form.errorCount", { count: failing.length }) : t("form.saveFailed")}>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {general.map((message) => (
              <li key={message}>{message}</li>
            ))}
            {failing.map((field) => (
              <li key={field.key}>
                <button
                  type="button"
                  className="cursor-pointer border-0 bg-transparent p-0 text-left text-danger-ink underline"
                  aria-label={t("section.summary.go", { label: labelOf(field) })}
                  onClick={() => {
                    focusField(field.key);
                  }}
                >
                  {labelOf(field)}
                </button>
                {t("section.summary.message", {
                  message: errors[field.key] ?? errors[errorKeys.find((key) => ownerOf(key) === field.key) ?? ""] ?? "",
                })}
              </li>
            ))}
          </ul>
        </Banner>
      ) : null}

      <form
        ref={form}
        noValidate
        onSubmit={(event) => {
          void submit(event);
        }}
        className="flex flex-col"
      >
        <div className="flex flex-col gap-4">
          {groupFields(view.fields).map((group) => (
            <Card key={group.id}>
              <CardHeader title={group.title} />
              <CardBody className="flex flex-col divide-y divide-line py-0">
                {group.fields.map((field) => (
                  <div key={field.key} className="py-4">
                    <FieldRenderer
                      field={field}
                      value={draft[field.key]}
                      loaded={view.values[field.key]}
                      state={view.state[field.key]}
                      error={errors[field.key]}
                      errors={errors}
                      readOnly={readOnly}
                      labelHidden={group.solo}
                      onChange={(next) => {
                        section.setField(field.key, next);
                      }}
                      onRestore={readOnly ? undefined : section.restoreField}
                    />
                  </div>
                ))}
              </CardBody>
            </Card>
          ))}
        </div>

        {readOnly ? null : (
          <div className="sticky bottom-0 -mx-4 mt-4 flex flex-wrap items-center gap-2 border-t border-line bg-surface px-4 py-3 sm:-mx-6 sm:px-6">
            <Button type="submit" busy={section.saving} disabled={!dirty}>
              {t("common.save")}
            </Button>
            <Button variant="secondary" disabled={!dirty || section.saving} onClick={section.discard}>
              {t("section.discard")}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setAskRestore(true);
              }}
            >
              {t("section.restoreAll")}
            </Button>
            <p role="status" className="m-0 ml-auto text-sm text-muted">
              {section.saved && !dirty ? t("section.saved") : ""}
              {skipped.length > 0 ? t("section.skipped", { names: skipped.join(", ") }) : ""}
            </p>
          </div>
        )}
      </form>

      <ConfirmDialog
        open={guard.open}
        onOpenChange={(open) => {
          if (!open) guard.stay();
        }}
        title={t("guard.title")}
        description={t("guard.description", { title: view.title })}
        destructive
        consequence={t("guard.consequence")}
        confirmLabel={t("guard.leave")}
        cancelLabel={t("guard.stay")}
        onConfirm={guard.leave}
      />
      <ConfirmDialog
        open={askRestore}
        onOpenChange={setAskRestore}
        title={t("section.restoreAll.title")}
        description={t("section.restoreAll.description", { title: view.title })}
        destructive
        consequence={t("section.restoreAll.consequence")}
        confirmLabel={t("section.restoreAll")}
        cancelLabel={t("common.cancel")}
        onConfirm={() => {
          const left = section.restoreAll();
          setSkipped(left.map((key) => view.fields.find((field) => field.key === key)?.label ?? key));
        }}
      />
      <ConfirmDialog
        open={askReplace}
        onOpenChange={setAskReplace}
        title={t("section.replace.title")}
        description={t("section.replace.description")}
        destructive
        consequence={t("section.replace.consequence")}
        confirmLabel={t("section.replace.confirm")}
        cancelLabel={t("common.cancel")}
        onConfirm={() => {
          void section.save({ confirmReplace: true });
        }}
      />
    </div>
  );
}
