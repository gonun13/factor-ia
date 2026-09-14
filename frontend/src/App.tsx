/**
 * Two screens and a hash for the route between them — a router dependency for
 * `#/p/<id>` would be more library than product. The hash keeps reload working,
 * which is the whole point of stage 3's verification.
 */

import { useEffect, useState } from "react";
import { FactoryCanvas } from "./screens/FactoryCanvas";
import { ProjectList } from "./screens/ProjectList";
import { Settings } from "./screens/Settings";

function currentProject(): string | null {
  const match = /^#\/p\/([^/]+)/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : null;
}

export default function App() {
  const [projectId, setProjectId] = useState<string | null>(currentProject);
  const [route, setRoute] = useState(() => window.location.hash);

  useEffect(() => {
    const onHashChange = () => {
      setProjectId(currentProject());
      setRoute(window.location.hash);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  // Its own route rather than a modal over the canvas: signing in is a
  // once-per-machine errand, not something done mid-run.
  if (route.startsWith("#/settings")) {
    return (
      <Settings
        onBack={() => {
          window.location.hash = "";
        }}
      />
    );
  }

  if (projectId === null) {
    return (
      <ProjectList
        onOpen={(id) => {
          window.location.hash = `#/p/${encodeURIComponent(id)}`;
        }}
      />
    );
  }

  return (
    <FactoryCanvas
      key={projectId}
      projectId={projectId}
      onBack={() => {
        window.location.hash = "";
      }}
    />
  );
}
