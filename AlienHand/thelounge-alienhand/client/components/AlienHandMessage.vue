<template>
	<article
		:class="[
			'alienhand-message',
			`alienhand-message--${row.orientation}`,
			{'alienhand-message--error': row.status === 'payload_error'},
		]"
		:data-message-uuid="row.message_uuid"
		:data-sender-type="row.sender_type"
	>
		<header class="alienhand-message__meta">
			<span class="alienhand-message__sender">{{ row.sender || row.sender_type }}</span>
			<time v-if="row.created_at" class="alienhand-message__time">{{ row.created_at }}</time>
		</header>
		<div class="alienhand-message__bubble">
			<p v-if="contentText" class="alienhand-message__text">{{ contentText }}</p>
			<template v-for="frame in row.frames" :key="frame.index">
				<pre
					v-if="frame.kind === 'code'"
					class="alienhand-frame alienhand-frame--code"
				><code>{{ frame.text }}</code></pre>
				<figure v-else-if="frame.kind === 'image'" class="alienhand-frame alienhand-frame--image">
					<img :src="frame.source" :alt="frame.alt || ''" loading="lazy" />
					<figcaption v-if="frame.alt">{{ frame.alt }}</figcaption>
				</figure>
				<a
					v-else-if="frame.kind === 'file' || frame.kind === 'link'"
					class="alienhand-frame alienhand-frame--link"
					:href="frame.source"
					rel="noreferrer"
					target="_blank"
				>
					{{ frame.label || frame.source }}
				</a>
				<pre v-else class="alienhand-frame alienhand-frame--unknown">{{
					JSON.stringify(frame.content || frame, null, 2)
				}}</pre>
			</template>
		</div>
	</article>
</template>

<script lang="ts">
import {computed, defineComponent, PropType} from "vue";

type AlienHandRenderFrame = {
	index: number;
	kind: string;
	text?: string;
	source?: string;
	alt?: string;
	label?: string;
	content?: Record<string, unknown>;
};

type AlienHandRenderRow = {
	message_uuid: string;
	sender: string;
	sender_type: string;
	status: string;
	orientation: "left" | "right" | "system";
	created_at?: string;
	content: {text?: string};
	frames: AlienHandRenderFrame[];
};

export default defineComponent({
	name: "AlienHandMessage",
	props: {
		row: {type: Object as PropType<AlienHandRenderRow>, required: true},
	},
	setup(props) {
		const contentText = computed(() => props.row.content?.text || "");

		return {contentText};
	},
});
</script>
