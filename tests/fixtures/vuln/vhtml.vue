<template>
  <!-- Covers UNSAFE_V_HTML -->
  <div v-html="userContent"></div> <!-- UNSAFE_V_HTML -->
</template>

<script>
export default {
  data: () => ({ userContent: '' }),
}
</script>
