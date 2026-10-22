import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useToast } from "./useToast";

/**
 * 導頁帶來的一次性成功訊息（navigate(path, { state: { message } })）在目的頁顯示成 toast，
 * 顯示後從 history state 拿掉，重新整理或上一頁不會重播。其他 state 欄位（例如 createdTemplateId）保留。
 */
export default function useRouteMessageToast() {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();
  const message = location.state?.message;
  useEffect(() => {
    if (!message) return;
    toast.success(message);
    const rest = { ...location.state };
    delete rest.message;
    navigate(`${location.pathname}${location.search}`, { replace: true, state: rest });
  }, [message, location.state, location.pathname, location.search, navigate, toast]);
}
