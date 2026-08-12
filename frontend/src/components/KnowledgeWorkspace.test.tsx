import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { KnowledgeLookupResult } from "../types";
import { KnowledgeResults } from "./KnowledgeWorkspace";

function resultFixture(): KnowledgeLookupResult {
  return {
    lookup_id: "KLOOK_SCOPE_001",
    intent: "knowledge_lookup",
    question_kind: "historical_match",
    status: "completed",
    plan: {
      intent: "knowledge_lookup",
      question_kind: "historical_match",
      action: "retrieve_historical_case",
      query: "Investigate a scratch observed after Cu CMP.",
      allowed_document_types: ["RCA_CASE"],
      reason: "Use governed causal candidates.",
      module: "Cu CMP",
      equipment_type: "CMP",
      operation: "6400",
      defect_type: "scratch",
      tags: ["scratch"],
      observation_scope: {
        source_lot_id: "LOT_A_001",
        product_id: "40N_SOC",
        detected_module: "Cu CMP",
        detected_operation: "6400",
        detected_equipment_id: "CMP_CU03",
        detected_equipment_type: "CMP",
        detected_at: "2026-07-08T20:45:00+00:00",
        symptom_types: ["scratch"],
        known_measurements: [],
        known_defect_attributes: ["radial"],
      },
      causal_search_scope: {
        mode: "causal_wide",
        hard_constraints: {
          module: "",
          equipment_type: "",
          operation: "",
          defect_type: "",
          tags: [],
        },
        soft_hints: {
          module: "Cu CMP",
          equipment_type: "CMP",
          operation: "6400",
          defect_type: "radial",
          tags: ["scratch"],
        },
        expansion_lanes: [
          {
            lane: "same_step",
            available: true,
            reason: "Use detected-step metadata as a bounded ranking lane.",
            modules: ["Cu CMP"],
            equipment_types: ["CMP"],
            route_distance: 0,
            shared_resource_types: [],
          },
          {
            lane: "upstream_route",
            available: true,
            reason: "Use earlier operations on the protected source Lot route.",
            modules: ["Thin Film"],
            equipment_types: [],
            route_distance: 1,
            shared_resource_types: [],
          },
          {
            lane: "shared_resource",
            available: false,
            reason: "No configured shared-resource exposure was found.",
            modules: [],
            equipment_types: [],
            route_distance: null,
            shared_resource_types: [],
          },
          {
            lane: "global_semantic",
            available: true,
            reason: "Search all approved RCA cases.",
            modules: [],
            equipment_types: [],
            route_distance: null,
            shared_resource_types: [],
          },
        ],
        available_lanes: ["same_step", "upstream_route", "global_semantic"],
        explicit_user_limits: [],
        time_boundary: "",
        candidate_budget: 20,
        lane_minimum: 1,
        scope_reason: "Observed metadata is a soft hint.",
      },
      explicit_module_limit: false,
      top_k: 5,
    },
    hits: [
      {
        rank: 1,
        document: {
          document_id: "DOC_CROSS_001",
          case_id: "CASE_CROSS_001",
          evaluation_asset_id: "CASE_CROSS_001",
          document_type: "RCA_CASE",
          title: "Upstream film particle transfer",
          content: "Approved Synthetic RCA reference.",
          module: "Thin Film",
          equipment_type: "CVD",
          operation: "5000",
          defect_type: "particle",
          tags: ["scratch"],
          source_format: "TEXT",
          content_sha256: "abc123",
          validation_status: "CONFIRMED",
          publication_policy: "BUILTIN_SYNTHETIC_SEED",
          source_candidate_id: null,
          created_at: "2026-07-01T00:00:00+00:00",
          schema_version: "1.0",
        },
        score: 0.88,
        matched_chunk_ids: ["CHUNK_CROSS_001"],
        excerpt: "The upstream film step transferred particles downstream.",
        evidence_id: "KEV_SCOPE_001",
        relevance_reason: "Matched through bounded causal lanes.",
        retrieval_strategy: "causal_lane_rrf+rrf_hybrid",
        score_components: { lexical: 0.7, vector: 0.8, fusion: 0.75 },
        calibrated_relevance: null,
        source_confidence: 0.8,
        candidate_lanes: ["upstream_route", "global_semantic"],
        scope_reasons: ["Use earlier operations on the protected source Lot route."],
        route_distance: 1,
        shared_resource_types: [],
        scope_fusion_score: 0.9,
      },
    ],
    agent_trace: [
      {
        agent: "knowledge",
        action: "retrieve_historical_case",
        execution_reason: "A historical reference can inform the open question.",
        inputs: {},
        output_evidence_ids: ["KEV_SCOPE_001"],
        stop_reason: "Bounded reference retrieval completed.",
      },
    ],
    answer_boundary: "Reference only; not root-cause confidence.",
    warnings: [],
    root_cause_conclusion: null,
    created_at: "2026-07-31T00:00:00+00:00",
  };
}

describe("Knowledge causal Scope trace", () => {
  it("renders observation, hard/soft boundaries, lane availability, and hit provenance", () => {
    const html = renderToStaticMarkup(
      <KnowledgeResults result={resultFixture()} />,
    );

    expect(html).toContain("Python-owned causal scope");
    expect(html).toContain("lot=LOT_A_001");
    expect(html).toContain("Hard constraints");
    expect(html).toContain("Approval and document type only");
    expect(html).toContain("same step");
    expect(html).toContain("shared resource");
    expect(html).toContain("Candidate lanes");
    expect(html).toContain("upstream route");
    expect(html).toContain("scope fusion 0.900");
    expect(html).toContain("not root-cause confidence");
  });

  it("labels the legacy feature-flag-off response as compatibility scope", () => {
    const result = resultFixture();
    result.plan.causal_search_scope = null;
    const html = renderToStaticMarkup(<KnowledgeResults result={result} />);

    expect(html).toContain("Compatibility scope");
    expect(html).toContain("Causal Scope is disabled");
  });
});
