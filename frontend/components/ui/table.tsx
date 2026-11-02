import { type HTMLAttributes, type TableHTMLAttributes } from "react";
import { cn } from "@/lib/utils";
export function Table({ className, ...props }: TableHTMLAttributes<HTMLTableElement>) { return <div className="overflow-auto"><table className={cn("w-full text-sm", className)} {...props} /></div>; }
export function TableHeader(props: HTMLAttributes<HTMLTableSectionElement>) { return <thead className="border-y border-border bg-muted/40" {...props} />; }
export function TableBody(props: HTMLAttributes<HTMLTableSectionElement>) { return <tbody {...props} />; }
export function TableRow({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) { return <tr className={cn("border-b border-border hover:bg-muted/30", className)} {...props} />; }
export function TableHead(props: HTMLAttributes<HTMLTableCellElement>) { return <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground" {...props} />; }
export function TableCell(props: HTMLAttributes<HTMLTableCellElement>) { return <td className="px-5 py-3 text-foreground" {...props} />; }
