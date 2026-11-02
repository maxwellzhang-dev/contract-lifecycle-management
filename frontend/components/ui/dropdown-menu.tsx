import { type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";
export function DropdownMenu({ className, ...props }: HTMLAttributes<HTMLDetailsElement>) { return <details className={cn("relative", className)} {...props} />; }
export function DropdownMenuTrigger(props: HTMLAttributes<HTMLElement>) { return <summary className="list-none cursor-pointer" {...props} />; }
export function DropdownMenuContent(props: HTMLAttributes<HTMLDivElement>) { return <div className="absolute right-0 z-50 mt-2 min-w-40 rounded-md border border-border bg-card p-1 shadow-lg" {...props} />; }
