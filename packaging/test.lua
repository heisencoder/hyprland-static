-- Smoke-test config for CI: exercises the option types a typical setup uses.
-- Hyprland 0.57 dropped hyprlang (hyprland.conf); configs are Lua now.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })

hl.config({
    general = {
        gaps_in          = 4,
        gaps_out         = 0,
        border_size      = 0,
        resize_on_border = true,
        layout           = "master",
    },
    master = {
        new_status  = "slave",
        orientation = "top",
        mfact       = 0.70,
    },
    group = {
        groupbar = {
            font_size = 14,
            col       = { active = "rgb(5588ff)" },
        },
    },
    animations = { enabled = true },
    misc = {
        background_color          = "rgb(111188)",
        on_focus_under_fullscreen = 1,
    },
    input = {
        kb_options   = "caps:escape",
        follow_mouse = 2,
    },
})

hl.curve("easeOutQuint", { type = "bezier", points = { {0.23, 1}, {0.32, 1} } })
hl.animation({ leaf = "windows", enabled = true, speed = 1.44, bezier = "easeOutQuint" })

hl.workspace_rule({ workspace = "1", monitor = "none" })

hl.bind("SUPER + Q", hl.dsp.window.close())
hl.bind("SUPER + CTRL + K", hl.dsp.window.move({ direction = "up", group_aware = true }))
hl.bind("SUPER + semicolon", hl.dsp.layout("addmaster"))
hl.bind("SUPER + mouse:273", hl.dsp.window.resize(), { mouse = true })

hl.window_rule({
    name             = "test-rule",
    match            = { class = "^test$" },
    no_initial_focus = true,
})
