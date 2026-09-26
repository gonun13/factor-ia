/**
 * The screens and a hash for the route between them — a router dependency for
 * a handful of routes would be more library than product. The hash keeps
 * reload working, which is the whole point of stage 3's verification.
 *
 *   #/                     the main page: projects, then factories
 *   #/settings             sign-in and the ssh key
 *   #/p/<project>          a project's main factory
 *   #/p/<project>/f/<node> …looking inside one of its factory nodes
 *   #/f/<factory>          a library factory's editor
 */

import { useEffect, useState } from "react";
import { FactoryEditor } from "./screens/FactoryEditor";
import { ProjectCanvas } from "./screens/ProjectCanvas";
import { ProjectList } from "./screens/ProjectList";
import { Settings } from "./screens/Settings";

type Route =
  | { screen: "list" }
  | { screen: "settings" }
  | { screen: "project"; projectId: string; drill: string | null }
  | { screen: "factory"; factoryId: string };

function parse(hash: string): Route {
  if (hash.startsWith("#/settings")) return { screen: "settings" };
  const factory = /^#\/f\/([^/]+)/.exec(hash);
  if (factory) return { screen: "factory", factoryId: decodeURIComponent(factory[1]) };
  const project = /^#\/p\/([^/]+)(?:\/f\/([^/]+))?/.exec(hash);
  if (project) {
    return {
      screen: "project",
      projectId: decodeURIComponent(project[1]),
      drill: project[2] ? decodeURIComponent(project[2]) : null,
    };
  }
  return { screen: "list" };
}

function go(hash: string): void {
  window.location.hash = hash;
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));

  useEffect(() => {
    const onHashChange = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  // Its own route rather than a modal over the canvas: signing in is a
  // once-per-machine errand, not something done mid-run.
  if (route.screen === "settings") return <Settings onBack={() => go("")} />;

  if (route.screen === "factory") {
    return (
      <FactoryEditor key={route.factoryId} factoryId={route.factoryId} onBack={() => go("")} />
    );
  }

  if (route.screen === "project") {
    const base = `#/p/${encodeURIComponent(route.projectId)}`;
    return (
      <ProjectCanvas
        // Keyed by the project only: looking inside a factory node and back out
        // must not remount the project canvas and lose its unsaved edits.
        key={route.projectId}
        projectId={route.projectId}
        drill={route.drill}
        onBack={() => go("")}
        onDrill={(refId) => go(refId ? `${base}/f/${encodeURIComponent(refId)}` : base)}
        onEditFactory={(factoryId) => go(`#/f/${encodeURIComponent(factoryId)}`)}
      />
    );
  }

  return (
    <ProjectList
      onOpen={(id) => go(`#/p/${encodeURIComponent(id)}`)}
      onOpenFactory={(id) => go(`#/f/${encodeURIComponent(id)}`)}
    />
  );
}
