import type { UseChatKitOptions } from "@openai/chatkit-react";

type ChatKitComposerAttachments = NonNullable<
  NonNullable<UseChatKitOptions["composer"]>["attachments"]
>;

export const CHATKIT_ATTACHMENT_MAX_SIZE_BYTES = 5 * 1024 * 1024;
export const CHATKIT_ATTACHMENT_MAX_COUNT = 10;

export const CHATKIT_ATTACHMENT_ACCEPT: NonNullable<
  ChatKitComposerAttachments["accept"]
> = {
  "text/plain": [".txt"],
  "text/markdown": [".md"],
  "application/json": [".json"],
  "text/csv": [".csv"],
  "text/x-log": [".log"],
  "application/pdf": [".pdf"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
  "image/webp": [".webp"],
  "image/gif": [".gif"],
};

export const buildChatKitAttachmentOptions = (): ChatKitComposerAttachments => ({
  enabled: true,
  accept: CHATKIT_ATTACHMENT_ACCEPT,
  maxSize: CHATKIT_ATTACHMENT_MAX_SIZE_BYTES,
  maxCount: CHATKIT_ATTACHMENT_MAX_COUNT,
});
