"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Tree, NodeRendererProps } from "react-arborist";
import { api, FsNode, ToolCall } from "@/lib/api";

type Props = {
  toolCall: ToolCall | null;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
};

export function FsTree({ toolCall, selectedPath, onSelectFile }: Props) {
  const toolCallId = toolCall?.id ?? null;
  const hasFsTree = toolCall?.has_fs_tree ?? false;
  const snapshotFailed = toolCall?.snapshot_failed ?? false;

  const [tree, setTree] = useState<FsNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });

  useEffect(() => {
    if (!toolCallId || !hasFsTree) {
      setTree(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getFsTree(toolCallId)
      .then((t) => {
        if (cancelled) return;
        setTree(t);
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [toolCallId, hasFsTree]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      setSize({ w: Math.floor(box.width), h: Math.floor(box.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => (tree ? [tree] : []), [tree]);

  const meta = tree?._meta;

  return (
    <section className="flex flex-col min-h-0 min-w-0 border-r border-neutral-800">
      <header className="px-4 py-2 border-b border-neutral-800 flex items-baseline justify-between gap-2">
        <h3 className="text-[10px] uppercase tracking-wide text-neutral-500">
          filesystem
        </h3>
        {meta && (
          <span className="text-[10px] text-neutral-500">
            {meta.entries.toLocaleString()} entries · {meta.elapsed_ms}ms
            {meta.truncated && " · truncated"}
          </span>
        )}
      </header>
      <div ref={containerRef} className="flex-1 min-h-0 min-w-0 overflow-hidden">
        {!toolCallId && (
          <Status>pick a tool call to view its sandbox fs</Status>
        )}
        {toolCallId && snapshotFailed && (
          <Status className="text-amber-400">
            snapshot failed for this tool call — fs unavailable
          </Status>
        )}
        {toolCallId && !snapshotFailed && !hasFsTree && (
          <Status>no fs tree captured</Status>
        )}
        {toolCallId && hasFsTree && loading && (
          <Status>loading fs tree…</Status>
        )}
        {error && <Status className="text-red-400">{error}</Status>}
        {tree && size.w > 0 && size.h > 0 && (
          <Tree<FsNode>
            data={data}
            idAccessor={(n) => n.path}
            childrenAccessor={(n) =>
              n.type === "dir" ? (n.children ?? []) : null
            }
            openByDefault
            disableMultiSelection
            disableEdit
            disableDrag
            disableDrop
            selection={selectedPath ?? undefined}
            width={size.w}
            height={size.h}
            rowHeight={24}
            indent={16}
            onActivate={(node) => {
              if (!node.isLeaf) return;
              onSelectFile(node.data.path);
            }}
          >
            {FsRow}
          </Tree>
        )}
      </div>
    </section>
  );
}

function FsRow({ node, style, dragHandle }: NodeRendererProps<FsNode>) {
  const isDir = !node.isLeaf;
  return (
    <div
      ref={dragHandle}
      style={style}
      onClick={() => {
        if (isDir) node.toggle();
        else node.activate();
      }}
      className={`flex items-center gap-1 px-2 text-xs cursor-pointer select-none whitespace-nowrap ${
        node.isSelected
          ? "bg-sky-500/20 text-sky-100"
          : "text-neutral-300 hover:bg-neutral-900"
      }`}
    >
      <span className="w-3 text-neutral-500 text-[10px]">
        {isDir ? (node.isOpen ? "▾" : "▸") : ""}
      </span>
      <span className={isDir ? "text-neutral-400" : ""}>
        {isDir ? "📁" : "📄"}
      </span>
      <span className="font-mono truncate flex-1">
        {node.data.name || node.data.path}
      </span>
      {node.data.error && (
        <span className="text-[10px] text-amber-400">err</span>
      )}
      {!isDir && typeof node.data.size === "number" && (
        <span className="text-[10px] text-neutral-500">
          {formatBytes(node.data.size)}
        </span>
      )}
    </div>
  );
}

function Status({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`p-4 text-xs text-neutral-500 ${className}`}
    >
      {children}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}K`;
  return `${(n / 1024 / 1024).toFixed(1)}M`;
}
