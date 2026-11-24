/** Demo customers. The API signs the chosen persona in (DEMO_MODE); "guest" must verify. */
export interface Persona {
  id: string;
  name: string;
  plan: string;
  status: string;
  scenario: string;
  suggestions: string[];
}

export const PERSONAS: Persona[] = [
  {
    id: "priya",
    name: "Priya Shah",
    plan: "Pro",
    status: "active",
    scenario: "Charged $29 twice for this month's subscription.",
    suggestions: [
      "I was charged twice this month. Can you fix it?",
      "How long does a refund take to show up?",
      "Can I pay for my plan with PayPal?",
    ],
  },
  {
    id: "marcus",
    name: "Marcus Lee",
    plan: "Free",
    status: "active",
    scenario: "Bought a $9 storage add-on. Free-plan purchases are final.",
    suggestions: ["Can I get my money back for the storage add-on?", "What does the Free plan include?"],
  },
  {
    id: "elena",
    name: "Elena Ruiz",
    plan: "Enterprise",
    status: "active",
    scenario: "Wants a $1,200 charge refunded, over the $500 automatic limit.",
    suggestions: ["Please refund the $1,200 seat expansion charge from last week.", "What's our support response time?"],
  },
  {
    id: "tom",
    name: "Tom Becker",
    plan: "Pro",
    status: "past due",
    scenario: "Latest payment failed, so the account is past due.",
    suggestions: ["Please refund last month's $29 charge.", "Why did my last payment fail?"],
  },
  {
    id: "guest",
    name: "Guest",
    plan: "Not signed in",
    status: "",
    scenario:
      "The agent asks for an email and billing ZIP before touching an account (try priya.shah@example.com, 94107). Three wrong tries lock the chat.",
    suggestions: [
      "I need a refund on my account.",
      "How do I reset two-factor authentication?",
      "What's the weather in Boston tomorrow?",
    ],
  },
];

export const DEFAULT_PERSONA = PERSONAS[0];

export function personaById(id: string): Persona {
  return PERSONAS.find((p) => p.id === id) ?? DEFAULT_PERSONA;
}

/** The persona id the API expects: null for an anonymous guest. */
export function apiPersonaId(persona: Persona): string | null {
  return persona.id === "guest" ? null : persona.id;
}
