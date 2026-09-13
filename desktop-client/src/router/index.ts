import { createRouter, createWebHashHistory, RouteRecordRaw } from "vue-router";

const Layout = () => import("@/layout/index.vue");

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
        meta: {
          title: "router.home.title",
          icon: "rocket-launch-rounded",
          keepAlive: false
        },
        component: () => import("@/views/home/index.vue")
      },
      {
        path: "/config",
        name: "Config",
        meta: {
          title: "router.config.title",
          icon: "settings",
          keepAlive: false,
          hidden: true
        },
        component: () => import("@/views/config/index.vue")
      },
      {
        path: "/about",
        name: "About",
        meta: {
          title: "router.about.title",
          icon: "info-sharp",
          keepAlive: false,
          hidden: true
        },
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
