import { type SelectHTMLAttributes } from "react";
import { cn } from "@/lib/utils";
export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn("h-10 rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/20", className)} {...props} />;
}
