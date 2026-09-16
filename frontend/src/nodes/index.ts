import type { EdgeTypes, NodeTypes } from "@xyflow/react";
import { AgentNode } from "./AgentNode";
import { HumanNode } from "./HumanNode";
import { BeltEdge } from "./BeltEdge";
import { InputNode } from "./InputNode";
import { MemoryNode } from "./MemoryNode";
import { OutputNode } from "./OutputNode";
import { BELT_EDGE } from "../graph";

/** Module-level constants: React Flow re-mounts every node if these change identity. */
export const nodeTypes: NodeTypes = {
  input: InputNode,
  agent: AgentNode,
  human: HumanNode,
  memory: MemoryNode,
  output: OutputNode,
};

export const edgeTypes: EdgeTypes = { [BELT_EDGE]: BeltEdge };

export {
  AgentGlyph,
  InputGlyph,
  OutputGlyph,
  CodebaseGlyph,
  HumanGlyph,
  MemoryGlyph,
  ReviewerGlyph,
} from "./Glyphs";
