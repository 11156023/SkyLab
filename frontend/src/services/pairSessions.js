import { apiDelete, apiGet, apiPost } from "./api";

/** 協作實驗室 Pair Mode（E5）：邀請同群組同學共同操作自己的 VM。 */
export const PairSessionsService = {
  /** 建立協作邀請 */
  create(vmid, inviteeUserId) {
    return apiPost("/api/v1/pair-sessions", {
      vmid,
      invitee_user_id: inviteeUserId,
    });
  },

  /** 我參與的協作 session（擁有者或受邀者） */
  listMine() {
    return apiGet("/api/v1/pair-sessions/mine");
  },

  /** 結束協作 */
  end(sessionId) {
    return apiDelete(`/api/v1/pair-sessions/${sessionId}`);
  },
};
