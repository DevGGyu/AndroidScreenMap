package com.example.notes.link

import com.example.notes.MainActivity

interface Launcher {
    fun openNoteDetail(id: Long)
    fun openHome()
    fun openWeb(url: String)
}

class LauncherImpl(private val ctx: Context) : Launcher {
    override fun openNoteDetail(id: Long) {
        ScreenHelper.startNoteDetail(ctx, id)
    }

    override fun openHome() {
        ctx.startActivity(Intent(ctx, MainActivity::class.java))
    }

    override fun openWeb(url: String) {
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    }
}
