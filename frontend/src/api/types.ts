// Friendly aliases over the generated OpenAPI types (src/api/schema.d.ts).
// Regenerate the schema with `npm run gen:types` whenever backend/app/schemas.py changes.
import type { components } from './schema';

type S = components['schemas'];

export type Requirement = S['Requirement'];
export type Quantity = S['Quantity'];
export type StructuredRequest = S['StructuredRequest'];
export type ParseRequest = S['ParseRequest'];
export type ParseResult = S['ParseResult'];
export type Ambiguity = S['Ambiguity'];
export type CreateRequest = S['CreateRequest'];
export type RequestResult = S['RequestResult'];
export type ChainNode = S['ChainNode'];
export type ChainEvent = S['ChainEvent'];
export type ChainTimeline = S['ChainTimeline'];
export type RejectNodeRequest = S['RejectNodeRequest'];
export type Candidate = S['Candidate'];
export type CandidateLine = S['CandidateLine'];
export type Totals = S['Totals'];
export type TrustScore = S['TrustScore'];
export type SelectResult = S['SelectResult'];
export type GateDecision = S['GateDecision'];
export type Quote = S['Quote'];
export type QuoteVerifyResult = S['QuoteVerifyResult'];
export type CreateOrderRequest = S['CreateOrderRequest'];
export type Order = S['Order'];
export type ApprovalCard = S['ApprovalCard'];
export type ApprovalDecision = S['ApprovalDecision'];
export type ApprovalDecisionResult = S['ApprovalDecisionResult'];
export type AuditEntry = S['AuditEntry'];
export type AuditVerifyResult = S['AuditVerifyResult'];
export type Fault = S['Fault'];
export type FaultUpdate = S['FaultUpdate'];
export type Facets = S['Facets'];
export type FacetItem = S['FacetItem'];
export type Profile = S['Profile'];
export type ProfileBody = S['ProfileBody'];
export type Protocol = S['Protocol'];
export type ProtocolBody = S['ProtocolBody'];
export type ProtocolDiff = S['ProtocolDiff'];
export type FieldChange = S['FieldChange'];
export type ProductPublic = S['ProductPublic'];
export type ErrorBody = S['ErrorBody'];

export type AgentConnection = S['AgentConnection'];
export type OrderSummary = S['OrderSummary'];
export type SeveralItem = S['SeveralItem'];
export type IssuedKey = S['IssuedKey'];
export type AgentKind = AgentConnection['kind'];
export type Outcome = Candidate['outcome_preview'];
export type Level = TrustScore['overall'];
export type RejectReason = RejectNodeRequest['reason'];
export type ErrorCode = S['ErrorDetail']['code'];
export type FaultName = Fault['name'];
