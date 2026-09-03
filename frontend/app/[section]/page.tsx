import { notFound } from "next/navigation";
import { RiffloomWorkbench } from "@/components/riffloom-workbench";
import type { PageKey } from "@/lib/domain";

const routeToPage: Record<string, PageKey> = {
  chat: "chat",
  collections: "collections",
  breakdowns: "breakdowns",
  creations: "creations",
  covers: "covers",
};

export default async function WorkspacePage({
  params,
  searchParams,
}: {
  params: Promise<{ section: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { section } = await params;
  const query = await searchParams;
  const page = routeToPage[section];

  if (!page) notFound();

  const sources = Array.isArray(query.source)
    ? query.source
    : query.source
      ? [query.source]
      : [];

  return (
    <RiffloomWorkbench
      initialPage={page}
      initialRecordId={typeof query.record === "string" ? query.record : undefined}
      initialTaskId={typeof query.task === "string" ? query.task : undefined}
      initialMode={typeof query.mode === "string" ? query.mode : undefined}
      initialSourceIds={sources}
    />
  );
}

export function generateStaticParams() {
  return Object.keys(routeToPage).map((section) => ({ section }));
}
