/**
 * A library factory's editor: the same canvas as a project's, with the palette
 * a factory is allowed and Simulate where Run would be.
 *
 * A factory is a fully automated block — agents and storage between six
 * connectors — that any project drops onto its line as a factory node
 * (`decisions/0029`). It has no input node, no output node and no person on it,
 * so the canvas offers none; its ways in and out are the connectors, which are
 * created with it and cannot be deleted.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";

import { api } from "../api";
import { FactoryGlyph } from "../nodes/Glyphs";
import { useRun } from "../run";
import type { Factory, LibraryFactorySummary } from "../types";
import { GraphEditor } from "./GraphEditor";
import { FACTORY_PALETTE, FACTORY_PALETTE_NOTE } from "./palette";
import { SimulateBox } from "./SimulateBox";

const NO_FACTORIES = new Map<string, LibraryFactorySummary>();

/** Where the last codebase simulated in is remembered, per factory. A per-viewer
 * convenience: losing it costs one pick from a list. */
function codebaseKey(factoryId: string): string {
  return `factoria:simulate:${factoryId}`;
}

function remembered(factoryId: string): string {
  try {
    return window.localStorage.getItem(codebaseKey(factoryId)) ?? "";
  } catch {
    return "";
  }
}

export function FactoryEditor({ factoryId, onBack }: { factoryId: string; onBack: () => void }) {
  return (
    <ReactFlowProvider>
      <FactoryEditorInner factoryId={factoryId} onBack={onBack} />
    </ReactFlowProvider>
  );
}

function FactoryEditorInner({ factoryId, onBack }: { factoryId: string; onBack: () => void }) {
  const [factory, setFactory] = useState<Factory | null>(null);
  const [codebaseId, setCodebaseId] = useState(() => remembered(factoryId));
  const run = useRun({ kind: "simulation", factoryId });

  useEffect(() => {
    setCodebaseId(remembered(factoryId));
  }, [factoryId]);

  const onCodebase = useCallback(
    (next: string) => {
      setCodebaseId(next);
      try {
        window.localStorage.setItem(codebaseKey(factoryId), next);
      } catch {
        /* private window, or storage refused: it is only a convenience */
      }
    },
    [factoryId],
  );

  const load = useMemo(() => () => api.libraryFactory(factoryId), [factoryId]);
  const save = useMemo(
    () => (graph: Parameters<typeof api.saveLibraryFactory>[1]) =>
      api.saveLibraryFactory(factoryId, graph),
    [factoryId],
  );

  return (
    <GraphEditor
      mode="factory"
      load={load}
      save={save}
      palette={FACTORY_PALETTE}
      paletteNote={FACTORY_PALETTE_NOTE}
      run={run}
      runtimes={run.runtimes}
      projectSettings={null}
      factories={NO_FACTORIES}
      factoriesLoaded={false}
      onLoaded={setFactory}
      heading={
        <>
          <button className="link-button" onClick={onBack} title="Back to the main page">
            ← Projects
          </button>
          <span className="brand">
            <span className="project-icon factory-icon" aria-hidden>
              <FactoryGlyph />
            </span>
            {factory?.name ?? factoryId}
            <small>· factory</small>
          </span>
        </>
      }
      controls={(controls) => (
        <SimulateBox
          controls={controls}
          run={run}
          codebaseId={codebaseId}
          onCodebase={onCodebase}
        />
      )}
    />
  );
}
