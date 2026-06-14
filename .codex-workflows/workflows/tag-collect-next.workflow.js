export default workflow({
  name: "tag-collect-next",
  version: "1",
  description:
    "Multi-agent review for the next vertical slice of the 1688 tag selection collector.",
  maxConcurrency: 3,
  maxAgents: 3,
  phases: [
    {
      id: "review",
      title: "Role Review",
      agents: [
        {
          id: "product",
          title: "Product Agent",
          prompt:
            "You are the product reviewer for scripts/capabilities/tag_collect. Read the PRD/progress docs and current implementation. Decide the next smallest user-valuable slice after the MVP. Focus on the user's requirements: tag-based selection, truthful data, detail verification before trusted decisions, export before manual review, and later multi-user use. Return concise Chinese output with must-have acceptance criteria and what should not be built yet. Do not edit files."
        },
        {
          id: "architect",
          title: "Architect Agent",
          prompt:
            "You are the architect reviewer for scripts/capabilities/tag_collect. Inspect service.py, web.py, cmd.py, prod_detail capability, and current data/export flow. Propose the safest architecture for the next slice without prematurely doing a full frontend/backend split. Focus on detail verification queue, field-level source/status, API contracts, export compatibility, and risk controls. Return concise Chinese output with concrete file/function recommendations. Do not edit files."
        },
        {
          id: "verification",
          title: "Verification Agent",
          prompt:
            "You are the verification/risk agent. Inspect tag_collect smoke tests and Web flow. Define regression tests and user-visible checks for the next slice. Focus on preventing unverified freight/refund/shipment fields from being treated as trusted, sample-mode testability without 1688 login, and download/export correctness. Return concise Chinese output with test cases and likely failure modes. Do not edit files."
        }
      ]
    }
  ]
});
