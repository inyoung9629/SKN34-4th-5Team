"use client";

import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { safeMemberReturnPath } from "@/lib/member-return-path";

type Props = { to: "/login" | "/signup"; label: string };

function AuthLink({ to, label }: Props) {
  const search = useSearchParams();
  const next = safeMemberReturnPath(search.get("next"));
  const query = new URLSearchParams();
  if (next) query.set("next", next);
  else if (search.get("next") === "admin") query.set("next", "admin");
  return <Link href={query.size ? `${to}?${query}` : to}>{label}</Link>;
}

export function MemberAuthSwitchLink(props: Props) {
  return <Suspense fallback={<Link href={props.to}>{props.label}</Link>}><AuthLink {...props} /></Suspense>;
}
