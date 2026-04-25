"use client";

import { Component, ReactNode } from "react";

type Props = {
  label: string;
  children: ReactNode;
  resetKey?: string | number | null;
};

type State = { error: Error | null };

// Renders a local fallback instead of crashing the whole page when a child
// throws during render — e.g. a malformed fs_tree_json from a failed snapshot.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`[${this.props.label}] render error`, error);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 p-4 text-xs text-red-400">
        <div>
          <span className="uppercase tracking-wide text-[10px] text-neutral-500 mr-2">
            {this.props.label}
          </span>
          render crashed
        </div>
        <pre className="font-mono text-[11px] text-neutral-400 max-w-full whitespace-pre-wrap">
          {this.state.error.message}
        </pre>
        <button
          onClick={() => this.setState({ error: null })}
          className="text-[10px] uppercase tracking-wide px-2 py-1 rounded border border-neutral-700 text-neutral-300 hover:bg-neutral-900"
        >
          retry
        </button>
      </div>
    );
  }
}
