import { Ajv2020 } from "ajv/dist/2020.js";
import { channelSchema } from "./channels-schema.generated.js";
import type { ChannelResponse, ChannelSaveRequest, ChannelOrigin } from "./channels.generated.js";
export type * from "./channels.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
export const isChannelResponse = ajv.compile<ChannelResponse>({ $defs: channelSchema.$defs, $ref: "#/$defs/ChannelResponse" });
export const isChannelSaveRequest = ajv.compile<ChannelSaveRequest>({ $defs: channelSchema.$defs, $ref: "#/$defs/ChannelSaveRequest" });
export const isChannelOrigin = ajv.compile<ChannelOrigin>({ $defs: channelSchema.$defs, $ref: "#/$defs/ChannelOrigin" });
