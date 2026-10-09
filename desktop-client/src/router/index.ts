import { createRouter, createWebHashHistory, RouteRecordRaw } from "vue-router";

const Layout = () => import("@/layout/index.vue");

/* 三個頁面都在同一個外框裡；登入沒有獨立頁，首頁的「開始連線」會帶使用者走瀏覽器登入 */
const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    redirect: "/home"
  },
  {
    path: "/",
    name: "Index",
    component: Layout,
    redirect: "/home",
    children: [
      {
        path: "/home",
        name: "Home",
        component: () => import("@/views/home/index.vue")
      },
      {
        path: "/config",
        name: "Config",
        component: () => import("@/views/config/index.vue")
      },
      {
        path: "/about",
        name: "About",
        component: () => import("@/views/about/index.vue")
      }
    ]
  },
  {
    path: "/:pathMatch(.*)*",
    redirect: "/"
  }
];

const router = createRouter({
  history: createWebHashHistory(),
  routes
});

export default router;
