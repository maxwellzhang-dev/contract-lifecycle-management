"use client";

import { useParams } from "next/navigation";
import { SigningPage } from "../../signatures";

export default function Sign() {
  const params = useParams<{ token: string }>();
  return <SigningPage token={params.token} />;
}
