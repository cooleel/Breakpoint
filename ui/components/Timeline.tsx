"use client";

import { useEffect, useRef } from "react";
import { Turn } from "@/lib/api";
import { TurnCard } from "./TurnCard";

export function Timeline({
  turns,
  selectedTurnId,
  onSelectTurn,
  scrollLeft,
  onScrollLeftChange,
}: {
  turns: Turn[];
  selectedTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  scrollLeft: number;
  onScrollLeftChange: (scrollLeft: number) => void;
}) {
  const selectedRef = useRef<HTMLButtonElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // True while we're programmatically syncing this row's scroll from shared
  // state — suppresses the echo scroll event that would otherwise clobber
  // shared state with this row's (possibly clamped) value.
  const isSyncingRef = useRef(false);

  useEffect(() => {
    selectedRef.current?.scrollIntoView({
      behavior: "smooth",
      inline: "center",
      block: "nearest",
    });
  }, [selectedTurnId]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    if (Math.abs(el.scrollLeft - scrollLeft) > 0.5) {
      isSyncingRef.current = true;
      el.scrollLeft = scrollLeft;
      requestAnimationFrame(() => {
        isSyncingRef.current = false;
      });
    }
  }, [scrollLeft]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = () => {
      if (isSyncingRef.current) return;
      onScrollLeftChange(el.scrollLeft);
    };
    el.addEventListener("scroll", handler, { passive: true });
    return () => el.removeEventListener("scroll", handler);
  }, [onScrollLeftChange]);

  if (turns.length === 0) {
    return (
      <div className="h-32 flex items-center justify-center text-xs text-neutral-500">
        no turns yet
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="overflow-x-auto overflow-y-hidden border-b border-neutral-800 bg-neutral-950"
    >
      <ol className="flex gap-2 px-4 py-4 min-w-max items-stretch">
        {turns.map((t) => {
          const selected = t.id === selectedTurnId;
          return (
            <li key={t.id}>
              <TurnCard
                ref={selected ? selectedRef : null}
                turn={t}
                selected={selected}
                ringColor="sky"
                onClick={() => onSelectTurn(t.id)}
              />
            </li>
          );
        })}
      </ol>
    </div>
  );
}
