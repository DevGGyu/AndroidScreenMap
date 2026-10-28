package com.example.notes.link

import com.example.notes.detail.NoteDetailActivity

object ScreenHelper {
    fun startNoteDetail(ctx: Context, id: Long) {
        ctx.startActivity(Intent(ctx, NoteDetailActivity::class.java).putExtra("id", id))
    }

    fun startNoteDetail(ctx: Context, id: Long, highlight: Boolean = false) {
        startNoteDetail(ctx, id)
    }
}
